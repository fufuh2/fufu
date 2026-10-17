"""POS adapter tested with a fake POS client. This is NOT a test against a real POS vendor."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Order
from app.services.integrations import sign
from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen, stock_of


class FakePos:
    def __init__(self, client, integration_id: str, secret: str):  # type: ignore[no-untyped-def]
        self.client, self.id, self.secret = client, integration_id, secret

    def send(
        self,
        payload: dict,
        *,
        secret: str | None = None,
        ts: int | None = None,
        raw: bytes | None = None,
    ):  # type: ignore[no-untyped-def]
        body = raw if raw is not None else json.dumps(payload).encode()
        stamp = str(ts if ts is not None else int(time.time()))
        return self.client.post(
            f"/api/v1/webhooks/pos/{self.id}",
            content=body,
            headers={
                "X-Timestamp": stamp,
                "X-Signature": sign(secret or self.secret, stamp, body),
                "Content-Type": "application/json",
            },
        )


def _setup(api: Api) -> tuple[dict[str, str], FakePos]:
    k = setup_kitchen(api)
    res = api.post("/integrations", {"name": "Kasa 1", "branch_id": k["branch"]})
    assert res.status_code == 201, res.text
    integ = res.json()
    api.client.put(
        f"/api/v1/integrations/{integ['integration']['id']}/mappings",
        json={"mappings": {"BRG-01": k["burger"]}},
        headers=api.headers,
    )
    return k, FakePos(api.client, integ["integration"]["id"], integ["signing_secret"])


def _order(ext: str, qty: int = 2) -> dict:
    return {
        "event": "order.created",
        "external_id": ext,
        "ordered_at": datetime.now(UTC).isoformat(),
        "lines": [{"code": "BRG-01", "quantity": qty}],
    }


def test_signed_order_creates_one_order(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, pos = _setup(api)
    p = _order("T-1")
    first = pos.send(p)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "processed"
    dup = pos.send(p)
    assert dup.status_code == 200 and dup.headers["Idempotent-Replayed"] == "true"
    assert dup.json()["order_id"] == first.json()["order_id"]
    assert stock_of(api, k["branch"], k["bun"]) == 28
    order = api.get(f"/orders/{first.json()['order_id']}").json()
    assert (order["channel"], order["external_ref"], order["net_total"]) == ("pos", "T-1", "500.00")


def test_bad_signatures_and_replays_rejected(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, pos = _setup(api)
    p = _order("T-2")
    assert pos.send(p, secret="wrong").status_code == 401
    assert pos.send(p, ts=int(time.time()) - 600).status_code == 401  # stale
    assert pos.send(p, ts=int(time.time()) + 600).status_code == 401  # future
    good_body = json.dumps(p).encode()
    stamp = str(int(time.time()))
    tampered = good_body.replace(b'"quantity": 2', b'"quantity": 20')
    res = client.post(
        f"/api/v1/webhooks/pos/{pos.id}",
        content=tampered,
        headers={"X-Timestamp": stamp, "X-Signature": sign(pos.secret, stamp, good_body)},
    )
    assert res.status_code == 401
    unknown = FakePos(client, "00000000-0000-0000-0000-000000000000", pos.secret)
    assert unknown.send(p).status_code == 401
    assert client.post(f"/api/v1/webhooks/pos/{pos.id}", content=good_body).status_code == 401
    assert stock_of(api, k["branch"], k["bun"]) == 30
    # rotation invalidates the old secret
    new = api.post(f"/integrations/{pos.id}/rotate-secret").json()["signing_secret"]
    assert pos.send(_order("T-3")).status_code == 401
    assert pos.send(_order("T-3"), secret=new).status_code == 200


def test_unmapped_code_is_kept_and_retried(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, pos = _setup(api)
    p = {**_order("T-4"), "lines": [{"code": "FRIES-9", "quantity": 1}]}
    res = pos.send(p)
    assert res.status_code == 422
    failed = api.get(f"/integrations/{pos.id}/events", params={"status": "failed"}).json()
    assert len(failed) == 1 and "FRIES-9" in failed[0]["error"]
    fries = api.post("/products", {"sku": "FRIES", "name": "Fries", "price": "60"}).json()["id"]
    api.client.put(
        f"/api/v1/integrations/{pos.id}/mappings",
        json={"mappings": {"FRIES-9": fries}},
        headers=api.headers,
    )
    retried = api.post(f"/integrations/{pos.id}/events/{failed[0]['id']}/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "processed"
    assert retried.json()["attempts"] == 2


def test_cancel_before_create_then_in_order(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, pos = _setup(api)
    cancel = {"event": "order.cancelled", "external_id": "T-5", "reason": "void"}
    assert pos.send(cancel).status_code == 422  # out of order: stored as failed
    assert pos.send(_order("T-5", 3)).status_code == 200
    assert stock_of(api, k["branch"], k["bun"]) == 27
    assert pos.send(cancel).status_code == 200  # redelivery processes it
    assert stock_of(api, k["branch"], k["bun"]) == 30
    again = pos.send(cancel)
    assert again.status_code == 200 and again.headers.get("Idempotent-Replayed") == "true"


def test_concurrent_duplicate_deliveries(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, pos = _setup(api)
    p = _order("T-6")
    body = json.dumps(p).encode()
    codes: list[int] = []
    barrier = threading.Barrier(5)

    def deliver() -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        c = TestClient(create_app())
        stamp = str(int(time.time()))
        barrier.wait()
        r = c.post(
            f"/api/v1/webhooks/pos/{pos.id}",
            content=body,
            headers={"X-Timestamp": stamp, "X-Signature": sign(pos.secret, stamp, body)},
        )
        codes.append(r.status_code)

    threads = [threading.Thread(target=deliver) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert codes == [200] * 5, codes
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert stock_of(api, k["branch"], k["bun"]) == 28
    assert session.execute(text("SELECT count(*), max(attempts) FROM pos_events")).one()[0] == 1


def test_integration_management_is_owner_only_and_isolated(client, session: Session) -> None:
    owner = new_tenant(session, client, "A")
    k, pos = _setup(owner)
    owner.post(
        "/users",
        {
            "email": "rm@example.com",
            "password": PASSWORD,
            "role": "regional_manager",
            "branch_ids": [k["branch"]],
        },
    )
    rm = Api(client, "rm@example.com")
    assert rm.post("/integrations", {"name": "x", "branch_id": k["branch"]}).status_code == 403
    other = new_tenant(session, client, "B")
    assert other.get(f"/integrations/{pos.id}/events").status_code == 404
    # the service user cannot log in
    email = session.execute(text("SELECT email FROM users WHERE email LIKE 'pos-%'")).scalar()
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": "x"}
    ).status_code in (401, 422)
    # deactivation stops deliveries
    owner.client.patch(
        f"/api/v1/integrations/{pos.id}", json={"is_active": False}, headers=owner.headers
    )
    assert pos.send(_order("T-7")).status_code == 401


def test_naive_or_missing_timestamp_in_payload(client, session: Session) -> None:
    api = new_tenant(session, client)
    _, pos = _setup(api)
    assert (
        pos.send(
            {
                "event": "order.created",
                "external_id": "T-8",
                "lines": [{"code": "BRG-01", "quantity": 1}],
            }
        ).status_code
        == 422
    )
    naive = {**_order("T-9"), "ordered_at": (datetime.now() - timedelta(minutes=1)).isoformat()}
    assert pos.send(naive).status_code == 422
    assert pos.send({}, raw=b"not json").status_code == 422
