import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_branch

P = "2026-08"


def _items(api: Api) -> dict[str, str]:
    return {i["name"]: i["id"] for i in api.get("/count-items").json()}


def _user(owner: Api, email: str, role: str, branches: list[str]) -> str:
    res = owner.post(
        "/users", {"email": email, "password": PASSWORD, "role": role, "branch_ids": branches}
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


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
    expired = jwt.encode({"sub": sub, "ver": 0, "exp": past}, secret, algorithm="HS256")
    forged = jwt.encode(
        {"sub": sub, "ver": 0, "exp": past + timedelta(days=1)}, "x" * 40, algorithm="HS256"
    )
    none_alg = jwt.encode({"sub": sub, "ver": 0}, None, algorithm="none")
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
    branch = setup_branch(a)
    items = _items(a)
    a.put(f"/branches/{branch}/monthly-counts/{P}", {"lines": []})
    b = new_tenant(session, client, "B")

    assert b.get("/branches").json() == []
    assert b.get("/count-items").json() == []
    assert b.get(f"/branches/{branch}/monthly-counts").status_code == 404
    assert b.get(f"/branches/{branch}/monthly-counts/{P}").status_code == 404
    assert b.get(f"/branches/{branch}/dashboard?period={P}").status_code == 404
    assert b.get(f"/branches/{branch}/monthly-counts/{P}/export.xlsx").status_code == 404
    assert b.patch(f"/count-items/{items['DANA KIYMA']}", {"unit_price": "1"}).status_code == 404
    bb = b.post("/branches", {"name": "B1"}).json()["id"]
    assert b.get(f"/branches/{bb}/count-items/{items['DANA KIYMA']}/history").status_code == 404
    assert b.get(f"/monthly-counts/overview?period={P}").json() == [
        {
            "branch_id": bb,
            "branch_name": "B1",
            "status": "yok",
            "counted_by_name": None,
            "totals": None,
        }
    ]
    assert all(e["entity_id"] != branch for e in b.get("/audit-events").json())
    assert [u["email"] for u in b.get("/users").json()] == [b.get("/me").json()["email"]]


def test_branch_scope_for_branch_users(client, session: Session) -> None:
    owner = new_tenant(session, client)
    branch = setup_branch(owner)
    other = owner.post("/branches", {"name": "Besiktas"}).json()["id"]
    _user(owner, "mgr@example.com", "branch_manager", [other])
    mgr = Api(client, "mgr@example.com")
    assert [x["id"] for x in mgr.get("/branches").json()] == [other]
    assert mgr.get(f"/branches/{branch}/monthly-counts/{P}").status_code == 404
    assert mgr.put(f"/branches/{branch}/monthly-counts/{P}", {"lines": []}).status_code == 404
    assert [r["branch_id"] for r in mgr.get(f"/monthly-counts/overview?period={P}").json()] == [
        other
    ]
    # No escalation: a branch manager manages neither users nor branches nor products.
    res = mgr.post("/users", {"email": "x@example.com", "password": PASSWORD, "role": "owner"})
    assert res.status_code == 403
    assert mgr.get("/users").status_code == 403
    assert mgr.post("/branches", {"name": "X"}).status_code == 403
    assert (
        mgr.post("/count-items", {"group_name": "G", "name": "N", "unit": "KG"}).status_code == 403
    )


def test_role_permissions_enforced(client, session: Session) -> None:
    owner = new_tenant(session, client)
    branch = setup_branch(owner)
    items = _items(owner)
    owner.put(f"/branches/{branch}/monthly-counts/{P}", {"lines": []})
    _user(owner, "view@example.com", "viewer", [branch])
    _user(owner, "count@example.com", "counter", [branch])
    view = Api(client, "view@example.com")
    count = Api(client, "count@example.com")
    line = {"lines": [{"item_id": items["PATATES"], "counted": "3"}]}

    assert view.get(f"/branches/{branch}/monthly-counts/{P}").status_code == 200
    assert view.get(f"/branches/{branch}/dashboard?period={P}").status_code == 200
    assert view.get("/audit-events").status_code == 200
    assert view.put(f"/branches/{branch}/monthly-counts/{P}", line).status_code == 403
    # A viewer cannot create a month by opening it.
    assert view.get(f"/branches/{branch}/monthly-counts/2026-09").status_code == 403

    assert count.put(f"/branches/{branch}/monthly-counts/{P}", line).status_code == 200
    assert count.post(f"/branches/{branch}/monthly-counts/{P}/close").status_code == 403
    assert count.get("/audit-events").status_code == 403
    assert count.patch(f"/count-items/{items['PATATES']}", {"unit_price": "9"}).status_code == 403


def test_unknown_role_is_rejected(client, session: Session) -> None:
    owner = new_tenant(session, client)
    branch = setup_branch(owner)
    res = owner.post(
        "/users",
        {"email": "k@example.com", "password": PASSWORD, "role": "kitchen", "branch_ids": [branch]},
    )
    assert res.status_code == 422


def test_deactivated_user_token_stops_working(client, session: Session) -> None:
    from sqlalchemy import update

    from app.models import User

    owner = new_tenant(session, client)
    branch = owner.post("/branches", {"name": "B"}).json()["id"]
    _user(owner, "fin@example.com", "viewer", [branch])
    fin = Api(client, "fin@example.com")
    assert fin.get("/branches").status_code == 200
    session.execute(update(User).where(User.email == "fin@example.com").values(is_active=False))
    session.commit()
    assert fin.get("/branches").status_code == 401


def test_errors_do_not_leak_internals(client, session: Session) -> None:
    api = new_tenant(session, client)
    res = api.get(f"/branches/{uuid.uuid4()}/monthly-counts/{P}")
    assert res.status_code == 404
    assert "sql" not in res.text.lower() and "traceback" not in res.text.lower()
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_removed_modules_are_gone(client, session: Session) -> None:
    api = new_tenant(session, client)
    for path in ("/items", "/products", "/suppliers", "/checklist-templates"):
        assert api.get(path).status_code == 404, path


def test_audit_log_is_branch_scoped(client, session: Session) -> None:
    owner = new_tenant(session, client)
    branch = setup_branch(owner)
    other = owner.post("/branches", {"name": "Besiktas"}).json()["id"]
    owner.put(f"/branches/{branch}/monthly-counts/{P}", {"lines": []})
    owner.put(f"/branches/{other}/monthly-counts/{P}", {"lines": []})
    _user(owner, "m2@example.com", "branch_manager", [other])
    mgr = Api(client, "m2@example.com")
    events = mgr.get("/audit-events").json()
    assert events, "manager should see events of their own branch"
    assert {e["data"]["branch_id"] for e in events} == {other}
    assert len(owner.get("/audit-events").json()) > len(events)
