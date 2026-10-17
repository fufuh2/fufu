"""Business rules and negative cases (section 10.5)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import new_tenant, setup_kitchen, stock_of

D = Decimal


def _period() -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }


def _order(api, k, key: str, qty: int = 1, discount: str = "0"):  # type: ignore[no-untyped-def]
    return api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": k["burger"], "quantity": qty, "discount": discount}]},
        headers={"Idempotency-Key": key},
    )


@pytest.mark.parametrize("table", ["stock_movements", "audit_events"])
def test_ledgers_are_append_only(client, session: Session, table: str) -> None:
    api = new_tenant(session, client)
    setup_kitchen(api)
    for stmt in (f"UPDATE {table} SET id = id", f"DELETE FROM {table}"):  # noqa: S608
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(text(stmt))
        session.rollback()


def test_idempotent_replay_and_conflict(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    first = _order(api, k, "abc", 2)
    assert first.status_code == 201
    replay = _order(api, k, "abc", 2)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    assert stock_of(api, k["branch"], k["bun"]) == D("28")  # consumed once
    different = _order(api, k, "abc", 3)
    assert different.status_code == 409
    missing = api.post(
        f"/branches/{k['branch']}/orders", {"lines": [{"product_id": k["burger"], "quantity": 1}]}
    )
    assert missing.status_code == 422


def test_cancel_reverses_stock_at_original_cost(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    order = _order(api, k, "c1", 3).json()
    assert stock_of(api, k["branch"], k["bun"]) == D("27")
    res = api.post(f"/orders/{order['id']}/cancel", {"reason": "customer left"})
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert stock_of(api, k["branch"], k["bun"]) == D("30")
    assert stock_of(api, k["branch"], k["beef"]) == D("10000")
    assert api.post(f"/orders/{order['id']}/cancel", {"reason": "again"}).status_code == 409
    margin = api.get(f"/branches/{k['branch']}/reports/margin", params=_period()).json()
    assert margin["lines"] == []
    var = api.get(f"/branches/{k['branch']}/reports/variance", params=_period()).json()
    assert all(D(x["theoretical_qty"]) == 0 for x in var["lines"])


def test_recipe_versions_do_not_rewrite_history(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    o1 = _order(api, k, "v1").json()
    assert D(o1["lines"][0]["cost_total"]) == D("65.00")  # 150 g*0.4 + 5
    res = api.post(
        f"/products/{k['burger']}/recipes",
        {
            "lines": [
                {"item_id": k["beef"], "quantity": "200", "unit": "g"},
                {"item_id": k["bun"], "quantity": "1", "unit": "pcs"},
            ]
        },
    )
    assert res.status_code == 201 and res.json()["version"] == 2
    o2 = _order(api, k, "v2").json()
    assert D(o2["lines"][0]["cost_total"]) == D("85.00")
    assert o1["lines"][0]["recipe_version_id"] != o2["lines"][0]["recipe_version_id"]
    again = api.get(f"/orders/{o1['id']}").json()
    assert D(again["lines"][0]["cost_total"]) == D("65.00")
    # Back-dating a recipe is refused
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    res = api.post(
        f"/products/{k['burger']}/recipes",
        {"valid_from": past, "lines": [{"item_id": k["beef"], "quantity": "1", "unit": "g"}]},
    )
    assert res.status_code == 422


def test_preparation_loss_increases_gross_issue(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    fries = api.post("/products", {"sku": "FRIES", "name": "Fries", "price": "60"}).json()["id"]
    potato = api.post("/items", {"sku": "POT", "name": "Potato", "base_unit": "g"}).json()["id"]
    api.post(
        f"/branches/{k['branch']}/receipts",
        {"lines": [{"item_id": potato, "quantity": "5", "unit": "kg", "unit_cost": "20"}]},
    )
    api.post(
        f"/products/{fries}/recipes",
        {"lines": [{"item_id": potato, "quantity": "180", "unit": "g", "loss_pct": "10"}]},
    )
    res = api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": fries, "quantity": 1}]},
        headers={"Idempotency-Key": "f1"},
    )
    assert res.status_code == 201
    assert stock_of(api, k["branch"], potato) == D("4800")  # 180 / 0.9 = 200 g issued


def test_missing_recipe_means_unknown_cost_not_zero(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    cola = api.post("/products", {"sku": "COLA", "name": "Cola", "price": "40"}).json()["id"]
    res = api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": cola, "quantity": 2}]},
        headers={"Idempotency-Key": "cola"},
    )
    assert res.status_code == 201
    assert res.json()["lines"][0]["cost_total"] is None
    _order(api, k, "b1")
    margin = api.get(f"/branches/{k['branch']}/reports/margin", params=_period()).json()
    lines = {x["sku"]: x for x in margin["lines"]}
    assert lines["COLA"]["status"] == "incomplete_cost"
    assert lines["COLA"]["cogs"] is None and lines["COLA"]["lines_without_recipe"] == 1
    assert lines["BURGER"]["status"] == "ok"
    assert margin["total"]["status"] == "incomplete_cost"
    assert margin["total"]["gross_profit"] is None


def test_fully_discounted_sale_margin_undefined(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    assert _order(api, k, "free", 1, "250").status_code == 201
    m = api.get(f"/branches/{k['branch']}/reports/margin", params=_period()).json()["total"]
    assert m["status"] == "undefined_zero_revenue"
    assert m["margin_pct"] is None
    assert D(m["gross_profit"]) == D("-65.00")
    assert _order(api, k, "too-much", 1, "250.01").status_code == 422


def test_sale_may_go_negative_but_waste_may_not(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    assert _order(api, k, "big", 31).status_code == 201  # 31 buns, only 30 on hand
    stock = {r["sku"]: r for r in api.get(f"/branches/{k['branch']}/stock").json()}
    assert D(stock["BUN"]["quantity"]) == D("-1")
    assert stock["BUN"]["negative"] is True
    res = api.post(
        f"/branches/{k['branch']}/waste",
        {"item_id": k["beef"], "quantity": "100", "unit": "kg", "reason": "unknown"},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "insufficient_stock"


def test_variance_without_count_is_unknown(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    _order(api, k, "o")
    var = api.get(f"/branches/{k['branch']}/reports/variance", params=_period()).json()
    beef = next(x for x in var["lines"] if x["sku"] == "BEEF")
    assert beef["unexplained_qty"] is None and beef["actual_qty"] is None
    assert beef["status"] == "no_count_in_period"


def test_low_stock_flag(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)  # beef min 2000 g
    api.post(
        f"/branches/{k['branch']}/waste",
        {"item_id": k["beef"], "quantity": "8.5", "unit": "kg", "reason": "spoilage_expiry"},
    )
    beef = next(r for r in api.get(f"/branches/{k['branch']}/stock").json() if r["sku"] == "BEEF")
    assert beef["below_min"] is True
    bun = next(r for r in api.get(f"/branches/{k['branch']}/stock").json() if r["sku"] == "BUN")
    assert bun["below_min"] is None  # no minimum defined -> unknown, not False


@pytest.mark.parametrize(
    "body",
    [
        {"item_id": "BEEF", "quantity": "-1", "unit": "g", "reason": "unknown"},
        {"item_id": "BEEF", "quantity": "0", "unit": "g", "reason": "unknown"},
        {"item_id": "BEEF", "quantity": "1e30", "unit": "g", "reason": "unknown"},
        {"item_id": "BEEF", "quantity": "1.00001", "unit": "g", "reason": "unknown"},
        {"item_id": "BEEF", "quantity": "1", "unit": "g", "reason": "theft"},
        {"item_id": "BEEF", "quantity": "1", "unit": "ml", "reason": "unknown"},
        {"item_id": "BEEF", "quantity": "1", "unit": "g"},
        {"item_id": "BEEF", "quantity": "1", "unit": "g", "reason": "unknown", "extra": 1},
        {
            "item_id": "BEEF",
            "quantity": "1",
            "unit": "g",
            "reason": "unknown",
            "occurred_at": "2999-01-01T00:00:00+00:00",
        },
        {
            "item_id": "BEEF",
            "quantity": "1",
            "unit": "g",
            "reason": "unknown",
            "occurred_at": "2024-01-01T00:00:00",
        },
        {"item_id": "not-a-uuid", "quantity": "1", "unit": "g", "reason": "unknown"},
    ],
)
def test_invalid_waste_inputs_rejected(client, session: Session, body: dict) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    if body["item_id"] == "BEEF":
        body = {**body, "item_id": k["beef"]}
    res = api.post(f"/branches/{k['branch']}/waste", body)
    assert res.status_code == 422, res.text
    assert stock_of(api, k["branch"], k["beef"]) == D("10000")


def test_unknown_ids_and_inactive_items(client, session: Session) -> None:
    import uuid

    from sqlalchemy import update

    from app.models import Item

    api = new_tenant(session, client)
    k = setup_kitchen(api)
    ghost = str(uuid.uuid4())
    assert api.get(f"/branches/{ghost}/stock").status_code == 404
    assert (
        api.post(
            f"/branches/{k['branch']}/waste",
            {"item_id": ghost, "quantity": "1", "unit": "g", "reason": "unknown"},
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/branches/{k['branch']}/orders",
            {"lines": [{"product_id": ghost, "quantity": 1}]},
            headers={"Idempotency-Key": "g"},
        ).status_code
        == 404
    )
    session.execute(update(Item).where(Item.sku == "BEEF").values(is_active=False))
    session.commit()
    assert (
        api.post(
            f"/branches/{k['branch']}/waste",
            {"item_id": k["beef"], "quantity": "1", "unit": "g", "reason": "unknown"},
        ).status_code
        == 404
    )


def test_duplicate_skus_and_units(client, session: Session) -> None:
    api = new_tenant(session, client)
    assert api.post("/items", {"sku": "A", "name": "a", "base_unit": "g"}).status_code == 201
    assert api.post("/items", {"sku": "A", "name": "a", "base_unit": "g"}).status_code == 409
    assert api.post("/items", {"sku": "B", "name": "b", "base_unit": "kg"}).status_code == 422
    assert (
        api.post(
            "/items", {"sku": "C", "name": "c", "base_unit": "g", "conversions": {"kg": "999"}}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/items", {"sku": "D", "name": "d", "base_unit": "g", "conversions": {"cup": "0"}}
        ).status_code
        == 422
    )
    assert api.post("/branches", {"name": "X"}).status_code == 201
    assert api.post("/branches", {"name": "X"}).status_code == 409


def test_count_rejects_duplicates_and_negative(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    dup = {"lines": [{"item_id": k["bun"], "quantity": "1", "unit": "pcs"}] * 2}
    assert api.post(f"/branches/{k['branch']}/counts", dup).status_code == 422
    neg = {"lines": [{"item_id": k["bun"], "quantity": "-1", "unit": "pcs"}]}
    assert api.post(f"/branches/{k['branch']}/counts", neg).status_code == 422
