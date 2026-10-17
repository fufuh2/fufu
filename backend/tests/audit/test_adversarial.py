"""Audit probes written against the *expected* behaviour. A failure here is a finding."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen, stock_of


def _ok_or_4xx(res) -> None:  # type: ignore[no-untyped-def]
    assert res.status_code < 500, f"{res.status_code}: {res.text}"


# --- AUD-NUM: numeric edge cases ------------------------------------------------------------


def test_tiny_conversion_rounding_to_zero_is_rejected(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    salt = api.post(
        "/items",
        {"sku": "SALT", "name": "Salt", "base_unit": "g", "conversions": {"pinch": "0.0001"}},
    ).json()["id"]
    b = k["branch"]
    res = api.post(
        f"/branches/{b}/receipts",
        {"lines": [{"item_id": salt, "quantity": "0.0001", "unit": "pinch", "unit_cost": "1"}]},
    )
    assert res.status_code == 422, res.text
    res = api.post(
        f"/branches/{b}/waste",
        {"item_id": salt, "quantity": "0.0001", "unit": "pinch", "reason": "unknown"},
    )
    assert res.status_code == 422, res.text
    res = api.post(
        f"/products/{k['burger']}/recipes",
        {"lines": [{"item_id": salt, "quantity": "0.0001", "unit": "pinch"}]},
    )
    assert res.status_code == 422, res.text


@pytest.mark.parametrize(
    "line",
    [
        {"quantity": "1000000000", "unit": "kg", "unit_cost": "1000000000"},  # total 1e18 money
        {"quantity": "1000000000", "unit": "pack", "unit_cost": "1"},  # 1e9 * 1e9 base qty
    ],
)
def test_overflowing_receipt_is_client_error(client, session: Session, line: dict) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    big = api.post(
        "/items",
        {"sku": "BIG", "name": "Big", "base_unit": "g", "conversions": {"pack": "1000000000"}},
    ).json()["id"]
    res = api.post(f"/branches/{k['branch']}/receipts", {"lines": [{"item_id": big, **line}]})
    _ok_or_4xx(res)
    assert res.status_code == 422


def test_overflowing_order_consumption_is_client_error(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    api.post(
        f"/products/{k['burger']}/recipes",
        {"lines": [{"item_id": k["beef"], "quantity": "1000000000", "unit": "kg"}]},
    )
    res = api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": k["burger"], "quantity": 10000}]},
        headers={"Idempotency-Key": "huge"},
    )
    _ok_or_4xx(res)
    # and nothing partial was written
    assert stock_of(api, k["branch"], k["beef"]) == 10000


# --- AUD-INT: backdating after a count ------------------------------------------------------


def test_backdated_waste_before_last_count_is_rejected(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    b = k["branch"]
    before = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    api.post(
        f"/branches/{b}/counts",
        {"lines": [{"item_id": k["beef"], "quantity": "9800", "unit": "g"}]},
    )  # 200 g missing, adjusted
    # The 200 g was in fact spilled two hours ago; recording it now would remove it twice.
    res = api.post(
        f"/branches/{b}/waste",
        {
            "item_id": k["beef"],
            "quantity": "200",
            "unit": "g",
            "reason": "spill_accident",
            "occurred_at": before,
        },
    )
    assert res.status_code == 422, res.text
    assert stock_of(api, b, k["beef"]) == 9800
    res = api.post(
        f"/branches/{b}/receipts",
        {
            "occurred_at": before,
            "lines": [{"item_id": k["beef"], "quantity": "1", "unit": "kg", "unit_cost": "400"}],
        },
    )
    assert res.status_code == 422, res.text


# --- AUD-SEC: security ----------------------------------------------------------------------


def test_login_is_rate_limited_per_account(client, session: Session) -> None:
    api = new_tenant(session, client)
    email = None
    from app.models import User

    email = session.query(User.email).scalar()
    codes = [
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": f"wrong-{i}"}
        ).status_code
        for i in range(8)
    ]
    assert 429 in codes, codes
    # even the right password is refused while locked
    res = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 429
    assert api  # owner session obtained before the attack still works
    assert api.get("/branches").status_code == 200


def test_failed_logins_are_audited(client, session: Session) -> None:
    new_tenant(session, client)
    client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    n = session.execute(
        text("SELECT count(*) FROM audit_events WHERE action = 'auth.login_failed'")
    ).scalar()
    assert n == 1


def test_validation_errors_do_not_echo_passwords(client, session: Session) -> None:
    api = new_tenant(session, client)
    secret = "short-pw"  # below the 12-char minimum
    res = api.post("/users", {"email": "a@example.com", "password": secret, "role": "owner"})
    assert res.status_code == 422
    assert secret not in res.text
    long_pw = "p" * 300
    res = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": long_pw})
    assert res.status_code == 422
    assert long_pw not in res.text


def test_api_docs_disabled_unless_explicitly_development(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.delenv("OPSX_ENV", raising=False)
    assert TestClient(create_app()).get("/docs").status_code == 404
    assert TestClient(create_app()).get("/openapi.json").status_code == 404
    monkeypatch.setenv("OPSX_ENV", "development")
    assert TestClient(create_app()).get("/docs").status_code == 200


def test_readiness_checks_database(client) -> None:
    res = client.get("/readyz")
    assert res.status_code == 200 and res.json()["database"] == "ok"


def test_mass_assignment_of_tenant_is_rejected(client, session: Session) -> None:
    a = new_tenant(session, client, "A")
    b = new_tenant(session, client, "B")
    a_tenant = session.execute(text("SELECT id FROM tenants WHERE name='A'")).scalar()
    res = b.post("/items", {"sku": "X", "name": "x", "base_unit": "g", "tenant_id": str(a_tenant)})
    assert res.status_code == 422
    assert a.get("/items").json() == []


def test_cross_tenant_ids_inside_bodies(client, session: Session) -> None:
    a = new_tenant(session, client, "A")
    ka = setup_kitchen(a)
    b = new_tenant(session, client, "B")
    kb = setup_kitchen(b)
    # B references A's item in recipe / receipt / count on B's own objects
    r1 = b.post(
        f"/products/{kb['burger']}/recipes",
        {"lines": [{"item_id": ka["beef"], "quantity": "1", "unit": "g"}]},
    )
    r2 = b.post(
        f"/branches/{kb['branch']}/receipts",
        {"lines": [{"item_id": ka["beef"], "quantity": "1", "unit": "g", "unit_cost": "1"}]},
    )
    r3 = b.post(
        f"/branches/{kb['branch']}/counts",
        {"lines": [{"item_id": ka["beef"], "quantity": "0", "unit": "g"}]},
    )
    r4 = b.post(
        "/users",
        {
            "email": "x@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [ka["branch"]],
        },
    )
    r5 = b.post(
        f"/products/{ka['burger']}/recipes",
        {"lines": [{"item_id": kb["beef"], "quantity": "1", "unit": "g"}]},
    )
    assert [r.status_code for r in (r1, r2, r3, r4, r5)] == [404] * 5
    assert stock_of(a, ka["branch"], ka["beef"]) == 10000


def test_injection_strings_are_stored_literally(client, session: Session) -> None:
    api = new_tenant(session, client)
    evil = "x'); DROP TABLE stock_movements; --<script>alert(1)</script>"
    res = api.post("/items", {"sku": "EVIL", "name": evil, "base_unit": "g"})
    assert res.status_code == 201
    assert res.headers["content-type"].startswith("application/json")
    assert any(i["name"] == evil for i in api.get("/items").json())
    assert session.execute(text("SELECT to_regclass('stock_movements')")).scalar() is not None
    assert (
        api.post("/items", {"sku": "a' OR '1'='1", "name": "x", "base_unit": "g"}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-01-01T00:00:00", "to": "2026-02-01T00:00:00"},  # naive
        {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},  # reversed
        {"from": "not-a-date", "to": "2026-01-01T00:00:00Z"},
    ],
)
def test_bad_report_periods(client, session: Session, params: dict) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    for kind in ("variance", "margin"):
        assert api.get(f"/branches/{k['branch']}/reports/{kind}", params=params).status_code == 422


# --- AUD-RBAC: role x endpoint matrix, expectations written independently from the code -------

ROLES = [
    "owner",
    "regional_manager",
    "branch_manager",
    "shift_lead",
    "purchasing",
    "kitchen",
    "quality",
    "finance",
    "auditor",
]
# endpoint -> roles that must be allowed (everyone else must get 403)
EXPECTED = {
    "create_branch": {"owner"},
    "create_user": {"owner"},
    "create_item": {"owner", "regional_manager"},
    "create_recipe": {"owner", "regional_manager"},
    "receive": {"owner", "regional_manager", "branch_manager", "purchasing"},
    "waste": {"owner", "regional_manager", "branch_manager", "shift_lead", "kitchen", "quality"},
    "count": {"owner", "regional_manager", "branch_manager", "shift_lead", "purchasing"},
    "order": {"owner", "regional_manager", "branch_manager", "shift_lead", "kitchen"},
    "margin": {"owner", "regional_manager", "branch_manager", "finance", "auditor"},
    "audit": {"owner", "regional_manager", "branch_manager", "auditor"},
}


def test_role_endpoint_matrix(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    b = k["branch"]
    now = datetime.now(UTC)
    period = {
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }
    mismatches = []
    for i, role in enumerate(ROLES):
        email = f"{role}@example.com"
        if role != "owner":
            res = owner.post(
                "/users", {"email": email, "password": PASSWORD, "role": role, "branch_ids": [b]}
            )
            assert res.status_code == 201, res.text
            api = Api(client, email)
        else:
            api = owner
        calls = {
            "create_branch": lambda a, i=i: a.post("/branches", {"name": f"N{i}"}),
            "create_user": lambda a, i=i: a.post(
                "/users",
                {
                    "email": f"n{i}@example.com",
                    "password": PASSWORD,
                    "role": "kitchen",
                    "branch_ids": [b],
                },
            ),
            "create_item": lambda a, i=i: a.post(
                "/items", {"sku": f"I{i}", "name": "i", "base_unit": "g"}
            ),
            "create_recipe": lambda a, i=i: a.post(
                f"/products/{k['burger']}/recipes",
                {"lines": [{"item_id": k["bun"], "quantity": "1", "unit": "pcs"}]},
            ),
            "receive": lambda a, i=i: a.post(
                f"/branches/{b}/receipts",
                {
                    "lines": [
                        {"item_id": k["bun"], "quantity": "1", "unit": "pcs", "unit_cost": "5"}
                    ]
                },
            ),
            "waste": lambda a, i=i: a.post(
                f"/branches/{b}/waste",
                {"item_id": k["bun"], "quantity": "1", "unit": "pcs", "reason": "unknown"},
            ),
            "count": lambda a, i=i: a.post(
                f"/branches/{b}/counts",
                {"lines": [{"item_id": k["bun"], "quantity": "20", "unit": "pcs"}]},
            ),
            "order": lambda a, i=i: a.post(
                f"/branches/{b}/orders",
                {"lines": [{"product_id": k["burger"], "quantity": 1}]},
                headers={"Idempotency-Key": f"r{i}"},
            ),
            "margin": lambda a, i=i: a.get(f"/branches/{b}/reports/margin", params=period),
            "audit": lambda a, i=i: a.get("/audit-events"),
        }
        for name, call in calls.items():
            code = call(api).status_code
            allowed = role in EXPECTED[name]
            if allowed and code == 403 or not allowed and code != 403:
                mismatches.append((role, name, code))
    assert mismatches == []


def test_jwt_secret_required(monkeypatch) -> None:
    from app.config import load_settings

    monkeypatch.setenv("OPSX_JWT_SECRET", "short")
    with pytest.raises(RuntimeError):
        load_settings()
    monkeypatch.setenv("OPSX_JWT_SECRET", os.environ.get("OPSX_JWT_SECRET", "") or "x" * 40)


def test_bootstrap_rejects_emails_login_would_reject(session: Session) -> None:
    from app.errors import Invalid
    from app.services.admin import bootstrap_tenant

    with pytest.raises(Invalid):
        bootstrap_tenant(session, name="T", owner_email="owner@audit.test", owner_password=PASSWORD)
    with pytest.raises(Invalid):
        bootstrap_tenant(session, name="T", owner_email="not-an-email", owner_password=PASSWORD)


def test_database_url_has_no_builtin_default(monkeypatch) -> None:
    from app.config import load_settings

    monkeypatch.delenv("OPSX_DATABASE_URL")
    with pytest.raises(RuntimeError, match="OPSX_DATABASE_URL"):
        load_settings()


def test_backdated_sale_before_count_does_not_double_consume(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    b = k["branch"]
    # Sold after the recipe took effect but synced only after the count: the count
    # already saw 29 buns.
    import time

    time.sleep(0.05)
    sold_at = datetime.now(UTC).isoformat()
    time.sleep(0.05)
    api.post(
        f"/branches/{b}/counts", {"lines": [{"item_id": k["bun"], "quantity": "29", "unit": "pcs"}]}
    )
    res = api.post(
        f"/branches/{b}/orders",
        {"ordered_at": sold_at, "lines": [{"product_id": k["burger"], "quantity": 1}]},
        headers={"Idempotency-Key": "late-sync"},
    )
    assert res.status_code == 201
    assert stock_of(api, b, k["bun"]) == 29  # FND-012: matches the physical count
    now = datetime.now(UTC)
    period = {
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }
    bun = next(
        x
        for x in api.get(f"/branches/{b}/reports/variance", params=period).json()["lines"]
        if x["sku"] == "BUN"
    )
    # The missing bun is explained by the sale, not left as an unexplained difference.
    assert (bun["theoretical_qty"], bun["unexplained_qty"]) == ("1.0000", "0.0000")
    order_id = res.json()["id"]
    assert api.post(f"/orders/{order_id}/cancel", {"reason": "void"}).status_code == 200
    assert stock_of(api, b, k["bun"]) == 29  # cancel reverses consumption and offset together


def test_hsts_only_in_production(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("OPSX_ENV", "production")
    assert (
        "max-age" in TestClient(create_app()).get("/healthz").headers["strict-transport-security"]
    )
    monkeypatch.setenv("OPSX_ENV", "development")
    assert "strict-transport-security" not in TestClient(create_app()).get("/healthz").headers
