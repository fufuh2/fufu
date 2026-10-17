"""Updates, deactivation, token revocation, recipe cost, pagination (FND-013/015/019/020)."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen


def test_price_change_applies_to_new_orders_only(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    o1 = api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": k["burger"], "quantity": 1}]},
        headers={"Idempotency-Key": "p1"},
    ).json()
    res = api.client.patch(
        f"/api/v1/products/{k['burger']}", json={"price": "300.00"}, headers=api.headers
    )
    assert res.status_code == 200 and res.json()["price"] == "300.00"
    o2 = api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": k["burger"], "quantity": 1}]},
        headers={"Idempotency-Key": "p2"},
    ).json()
    assert (o1["net_total"], o2["net_total"]) == ("250.00", "300.00")
    assert api.get(f"/orders/{o1['id']}").json()["net_total"] == "250.00"
    ev = session.execute(
        text("SELECT data FROM audit_events WHERE action='catalog.product.update'")
    ).scalar()
    assert ev["before"]["price"] == "250.00" and ev["after"]["price"] == "300.00"


def test_deactivated_product_and_item_cannot_be_used(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    patch = api.client.patch
    assert (
        patch(
            f"/api/v1/products/{k['burger']}", json={"is_active": False}, headers=api.headers
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/branches/{k['branch']}/orders",
            {"lines": [{"product_id": k["burger"], "quantity": 1}]},
            headers={"Idempotency-Key": "x"},
        ).status_code
        == 404
    )
    assert (
        patch(
            f"/api/v1/items/{k['beef']}", json={"is_active": False}, headers=api.headers
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/branches/{k['branch']}/waste",
            {"item_id": k["beef"], "quantity": "1", "unit": "g", "reason": "unknown"},
        ).status_code
        == 404
    )
    # base unit / sku are not patchable
    assert (
        patch(
            f"/api/v1/items/{k['beef']}", json={"base_unit": "ml"}, headers=api.headers
        ).status_code
        == 422
    )
    res = patch(f"/api/v1/items/{k['bun']}", json={"min_stock": None}, headers=api.headers)
    assert res.status_code == 200 and res.json()["min_stock"] is None


def test_branch_deactivation_blocks_writes(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    res = api.client.patch(
        f"/api/v1/branches/{k['branch']}", json={"is_active": False}, headers=api.headers
    )
    assert res.status_code == 200 and res.json()["is_active"] is False
    assert (
        api.post(
            f"/branches/{k['branch']}/receipts",
            {"lines": [{"item_id": k["bun"], "quantity": "1", "unit": "pcs", "unit_cost": "1"}]},
        ).status_code
        == 404
    )
    assert api.get(f"/branches/{k['branch']}/stock").status_code == 200  # history stays readable


def test_role_change_and_deactivation_revoke_tokens(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    uid = owner.post(
        "/users",
        {
            "email": "u@example.com",
            "password": PASSWORD,
            "role": "branch_manager",
            "branch_ids": [k["branch"]],
        },
    ).json()["id"]
    user = Api(client, "u@example.com")
    assert user.get("/audit-events").status_code == 200
    res = client.patch(f"/api/v1/users/{uid}", json={"role": "kitchen"}, headers=owner.headers)
    assert res.status_code == 200 and res.json()["role"] == "kitchen"
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


def test_recipe_cost_endpoint(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    c = api.get(f"/products/{k['burger']}/cost", params={"branch_id": k["branch"]}).json()
    # 150 g * 0.40 + 1 * 5.00 = 65.00 ; 65 / 250 = 26.00 %
    assert (c["unit_cost"], c["food_cost_pct"], c["status"]) == ("65.00", "26.00", "ok")
    other = api.post("/branches", {"name": "Empty"}).json()["id"]
    c2 = api.get(f"/products/{k['burger']}/cost", params={"branch_id": other}).json()
    assert c2["unit_cost"] is None and c2["status"] == "incomplete_cost"
    cola = api.post("/products", {"sku": "COLA", "name": "Cola", "price": "40"}).json()["id"]
    c3 = api.get(f"/products/{cola}/cost", params={"branch_id": k["branch"]}).json()
    assert c3["status"] == "no_recipe"


def test_pagination(client, session: Session) -> None:
    api = new_tenant(session, client)
    for i in range(5):
        api.post("/items", {"sku": f"S{i}", "name": "n", "base_unit": "g"})
    page = api.get("/items", params={"limit": 2, "offset": 2}).json()
    assert [x["sku"] for x in page] == ["S2", "S3"]
    assert api.get("/items", params={"limit": 501}).status_code == 422
    assert Decimal(len(api.get("/products").json())) == 0
