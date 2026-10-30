from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_branch

P = "2026-08"


def test_branch_deactivation_blocks_writes(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    api.put(f"/branches/{branch}/monthly-counts/{P}", {"lines": []})
    res = api.patch(f"/branches/{branch}", {"is_active": False})
    assert res.status_code == 200 and res.json()["is_active"] is False
    assert api.put(f"/branches/{branch}/monthly-counts/{P}", {"lines": []}).status_code == 404
    assert api.get(f"/branches/{branch}/monthly-counts/2026-09").status_code == 404
    # history stays readable
    assert api.get(f"/branches/{branch}/monthly-counts/{P}").status_code == 200


def test_role_change_and_deactivation_revoke_tokens(client, session: Session) -> None:
    owner = new_tenant(session, client)
    branch = setup_branch(owner)
    uid = owner.post(
        "/users",
        {
            "email": "u@example.com",
            "password": PASSWORD,
            "role": "branch_manager",
            "branch_ids": [branch],
        },
    ).json()["id"]
    user = Api(client, "u@example.com")
    assert user.get("/audit-events").status_code == 200
    res = client.patch(f"/api/v1/users/{uid}", json={"role": "counter"}, headers=owner.headers)
    assert res.status_code == 200 and res.json()["role"] == "counter"
    assert user.get("/branches").status_code == 401  # old token revoked
    user = Api(client, "u@example.com")
    assert user.get("/audit-events").status_code == 403  # new role applies
    # owner cannot lock themselves out
    me = session.execute(text("SELECT id FROM users WHERE role='owner'")).scalar()
    assert (
        client.patch(
            f"/api/v1/users/{me}", json={"is_active": False}, headers=owner.headers
        ).status_code
        == 422
    )
    # branch-scoped role needs a branch
    assert (
        client.patch(
            f"/api/v1/users/{uid}", json={"branch_ids": []}, headers=owner.headers
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/users/{uid}", json={"is_active": False}, headers=owner.headers
        ).status_code
        == 200
    )
    r = client.post("/api/v1/auth/login", json={"email": "u@example.com", "password": PASSWORD})
    assert r.status_code == 401


def test_password_change_and_logout_all(client, session: Session) -> None:
    owner = new_tenant(session, client)
    email = session.execute(text("SELECT email FROM users")).scalar()
    other_session = Api(client, email)
    bad = owner.post(
        "/auth/password", {"current_password": "wrong-wrong-wrong", "new_password": "x" * 12}
    )
    assert bad.status_code == 401
    ok = owner.post(
        "/auth/password", {"current_password": PASSWORD, "new_password": "brand-new-password-1"}
    )
    assert ok.status_code == 204
    assert other_session.get("/branches").status_code == 401
    assert owner.get("/branches").status_code == 401
    fresh = Api(client, email, "brand-new-password-1")
    assert fresh.post("/auth/logout-all").status_code == 204
    assert fresh.get("/branches").status_code == 401


def test_token_without_version_is_rejected(client, session: Session) -> None:
    import os
    from datetime import UTC, datetime, timedelta

    import jwt

    new_tenant(session, client)
    uid = session.execute(text("SELECT id FROM users")).scalar()
    legacy = jwt.encode(
        {"sub": str(uid), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        os.environ["OPSX_JWT_SECRET"],
        algorithm="HS256",
    )
    assert (
        client.get("/api/v1/branches", headers={"Authorization": f"Bearer {legacy}"}).status_code
        == 401
    )
