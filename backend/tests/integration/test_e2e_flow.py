"""Section 10.4 scenario end to end through the HTTP API, checking API, DB and arithmetic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, StockBalance, StockMovement
from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen, stock_of

D = Decimal


def _period() -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }


def test_full_operational_flow(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    b = k["branch"]

    # Receipt converted to base units: 10 kg -> 10000 g, 5 packs -> 30 pcs
    assert stock_of(api, b, k["beef"]) == D("10000")
    assert stock_of(api, b, k["bun"]) == D("30")

    # Sale: 4 burgers with a 100.00 discount on the line
    res = api.post(
        f"/branches/{b}/orders",
        {
            "channel": "test",
            "lines": [{"product_id": k["burger"], "quantity": 4, "discount": "100"}],
        },
        headers={"Idempotency-Key": "order-1"},
    )
    assert res.status_code == 201, res.text
    order = res.json()
    assert D(order["net_total"]) == D("900.00")
    # cost = 600 g * 0.40 + 4 buns * 5.00 = 260.00
    assert D(order["lines"][0]["cost_total"]) == D("260.00")
    assert stock_of(api, b, k["beef"]) == D("9400")
    assert stock_of(api, b, k["bun"]) == D("26")

    # Waste: 200 g spoiled beef
    res = api.post(
        f"/branches/{b}/waste",
        {"item_id": k["beef"], "quantity": "200", "unit": "g", "reason": "spoilage_expiry"},
    )
    assert res.status_code == 201, res.text
    assert D(res.json()["total_cost"]) == D("-80.00")

    # Physical count: 200 g of beef missing without explanation, buns match
    res = api.post(
        f"/branches/{b}/counts",
        {
            "lines": [
                {"item_id": k["beef"], "quantity": "9", "unit": "kg"},
                {"item_id": k["bun"], "quantity": "26", "unit": "pcs"},
            ]
        },
    )
    assert res.status_code == 201, res.text
    assert stock_of(api, b, k["beef"]) == D("9000")

    var = api.get(f"/branches/{b}/reports/variance", params=_period()).json()
    lines = {x["sku"]: x for x in var["lines"]}
    assert var["generated_at"] and var["source"].startswith("stock_movements")
    beef = lines["BEEF"]
    assert D(beef["theoretical_qty"]) == D("600")
    assert D(beef["recorded_waste_qty"]) == D("200")
    assert D(beef["unexplained_qty"]) == D("200")
    assert D(beef["unexplained_cost"]) == D("80.00")
    assert D(beef["actual_qty"]) == D("1000")
    assert beef["status"] == "needs_review"
    assert beef["waste_by_reason"] == {"spoilage_expiry": "200.0000"}
    assert lines["BUN"]["status"] == "ok"
    assert D(lines["BUN"]["unexplained_qty"]) == 0

    margin = api.get(f"/branches/{b}/reports/margin", params=_period()).json()
    assert D(margin["total"]["net_revenue"]) == D("900.00")
    assert D(margin["total"]["cogs"]) == D("260.00")
    assert D(margin["total"]["gross_profit"]) == D("640.00")
    assert D(margin["total"]["margin_pct"]) == D("71.11")
    assert margin["total"]["status"] == "ok"
    assert margin["generated_at"] and "net of VAT" in margin["source"]

    # Unauthorised access: a kitchen user of this branch cannot read finance reports...
    res = api.post(
        "/users",
        {"email": "cook@example.com", "password": PASSWORD, "role": "kitchen", "branch_ids": [b]},
    )
    assert res.status_code == 201, res.text
    cook = Api(client, "cook@example.com")
    assert cook.get(f"/branches/{b}/reports/margin", params=_period()).status_code == 403
    assert (
        cook.post(
            f"/branches/{b}/receipts",
            {"lines": [{"item_id": k["beef"], "quantity": "1", "unit": "kg", "unit_cost": "1"}]},
        ).status_code
        == 403
    )
    # ...and another tenant cannot see the branch at all.
    other = new_tenant(session, client, "Other")
    assert other.get(f"/branches/{b}/stock").status_code == 404

    # DB invariant: balance == sum of ledger rows, for every item
    for bal in session.scalars(select(StockBalance)):
        total = session.scalar(
            select(func.sum(StockMovement.quantity_base)).where(
                StockMovement.branch_id == bal.branch_id, StockMovement.item_id == bal.item_id
            )
        )
        assert total == bal.quantity

    actions = set(session.scalars(select(AuditEvent.action)))
    assert {
        "branch.create",
        "catalog.item.create",
        "recipe.version.create",
        "stock.receive",
        "order.create",
        "stock.waste",
        "stock.count",
        "user.create",
    } <= actions
