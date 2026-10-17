import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen


def test_requires_authentication(client) -> None:
    assert client.get("/api/v1/branches").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/branches", headers=bad).status_code == 401


def test_expired_and_forged_tokens_rejected(client, session: Session) -> None:
    api = new_tenant(session, client)
    token = api.headers["Authorization"].split()[1]
    sub = jwt.decode(token, options={"verify_signature": False})["sub"]
    past = datetime.now(UTC) - timedelta(hours=1)
    secret = os.environ["OPSX_JWT_SECRET"]
    expired = jwt.encode({"sub": sub, "exp": past}, secret, algorithm="HS256")
    forged = jwt.encode({"sub": sub, "exp": past + timedelta(days=1)}, "x" * 40, algorithm="HS256")
    none_alg = jwt.encode({"sub": sub}, None, algorithm="none")
    for t in (expired, forged, none_alg):
        res = client.get("/api/v1/branches", headers={"Authorization": f"Bearer {t}"})
        assert res.status_code == 401


def test_login_does_not_reveal_which_part_was_wrong(client, session: Session) -> None:
    new_tenant(session, client)
    a = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert a.status_code == 401
    assert a.json()["detail"]["message"] == "invalid credentials"


def test_tenant_isolation(client, session: Session) -> None:
    a = new_tenant(session, client, "A")
    k = setup_kitchen(a)
    order = a.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": k["burger"], "quantity": 1}]},
        headers={"Idempotency-Key": "k1"},
    ).json()
    b = new_tenant(session, client, "B")

    assert b.get("/branches").json() == []
    assert b.get("/items").json() == []
    assert b.get("/products").json() == []
    assert b.get(f"/branches/{k['branch']}/stock").status_code == 404
    assert b.get(f"/branches/{k['branch']}/movements").status_code == 404
    assert b.get(f"/orders/{order['id']}").status_code == 404
    assert b.post(f"/orders/{order['id']}/cancel", {"reason": "x"}).status_code == 404
    assert (
        b.post(
            f"/branches/{k['branch']}/waste",
            {"item_id": k["beef"], "quantity": "1", "unit": "g", "reason": "unknown"},
        ).status_code
        == 404
    )
    # B uses its own branch but A's item / product ids
    bb = b.post("/branches", {"name": "B1"}).json()["id"]
    assert (
        b.post(
            f"/branches/{bb}/waste",
            {"item_id": k["beef"], "quantity": "1", "unit": "g", "reason": "unknown"},
        ).status_code
        == 404
    )
    assert (
        b.post(
            f"/branches/{bb}/orders",
            {"lines": [{"product_id": k["burger"], "quantity": 1}]},
            headers={"Idempotency-Key": "k1"},
        ).status_code
        == 404
    )
    # Same idempotency key in another tenant does not collide
    assert a.get(f"/orders/{order['id']}").status_code == 200
    assert all(e["entity_id"] != order["id"] for e in b.get("/audit-events").json())


def test_branch_scope_for_branch_users(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    other_branch = owner.post("/branches", {"name": "Besiktas"}).json()["id"]
    owner.post(
        "/users",
        {
            "email": "mgr@example.com",
            "password": PASSWORD,
            "role": "branch_manager",
            "branch_ids": [other_branch],
        },
    )
    mgr = Api(client, "mgr@example.com")
    assert [x["id"] for x in mgr.get("/branches").json()] == [other_branch]
    assert mgr.get(f"/branches/{k['branch']}/stock").status_code == 404
    assert (
        mgr.post(
            f"/branches/{k['branch']}/counts",
            {"lines": [{"item_id": k["beef"], "quantity": "0", "unit": "g"}]},
        ).status_code
        == 404
    )
    # Branch manager cannot escalate: no user management
    res = mgr.post("/users", {"email": "x@example.com", "password": PASSWORD, "role": "owner"})
    assert res.status_code == 403


def test_role_permissions_enforced(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    owner.post(
        "/users",
        {
            "email": "aud@example.com",
            "password": PASSWORD,
            "role": "auditor",
            "branch_ids": [k["branch"]],
        },
    )
    aud = Api(client, "aud@example.com")
    assert aud.get(f"/branches/{k['branch']}/stock").status_code == 200
    assert aud.get("/audit-events").status_code == 200
    assert (
        aud.post(
            f"/branches/{k['branch']}/orders",
            {"lines": [{"product_id": k["burger"], "quantity": 1}]},
            headers={"Idempotency-Key": "x"},
        ).status_code
        == 403
    )
    assert aud.post("/items", {"sku": "X", "name": "x", "base_unit": "g"}).status_code == 403


def test_deactivated_user_token_stops_working(client, session: Session) -> None:
    from sqlalchemy import update

    from app.models import User

    owner = new_tenant(session, client)
    owner.post("/branches", {"name": "B"})
    owner.post(
        "/users",
        {
            "email": "fin@example.com",
            "password": PASSWORD,
            "role": "finance",
            "branch_ids": [owner.get("/branches").json()[0]["id"]],
        },
    )
    fin = Api(client, "fin@example.com")
    assert fin.get("/branches").status_code == 200
    session.execute(update(User).where(User.email == "fin@example.com").values(is_active=False))
    session.commit()
    assert fin.get("/branches").status_code == 401


def test_errors_do_not_leak_internals(client, session: Session) -> None:
    api = new_tenant(session, client)
    res = api.get(f"/orders/{uuid.uuid4()}")
    assert res.status_code == 404
    assert "sql" not in res.text.lower() and "traceback" not in res.text.lower()
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_audit_log_is_branch_scoped(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    other = owner.post("/branches", {"name": "Besiktas"}).json()["id"]
    owner.post(
        f"/branches/{other}/receipts",
        {"lines": [{"item_id": k["bun"], "quantity": "1", "unit": "pcs", "unit_cost": "5"}]},
    )
    owner.post(
        "/users",
        {
            "email": "m2@example.com",
            "password": PASSWORD,
            "role": "branch_manager",
            "branch_ids": [other],
        },
    )
    mgr = Api(client, "m2@example.com")
    events = mgr.get("/audit-events").json()
    assert events, "manager should see events of their own branch"
    assert {e["data"]["branch_id"] for e in events} == {other}
    assert len(owner.get("/audit-events").json()) > len(events)
