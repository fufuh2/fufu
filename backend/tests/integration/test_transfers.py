from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StockBalance, StockMovement
from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen, stock_of


def _two_branches(api: Api) -> tuple[dict[str, str], str]:
    k = setup_kitchen(api)  # Kadikoy: 10 000 g beef @0.40, 30 buns @5
    other = api.post("/branches", {"name": "Besiktas"}).json()["id"]
    return k, other


def _ship(api: Api, k: dict[str, str], to: str, beef: str = "2", buns: str = "6"):  # type: ignore[no-untyped-def]
    return api.post(
        f"/branches/{k['branch']}/transfers",
        {
            "to_branch_id": to,
            "lines": [
                {"item_id": k["beef"], "quantity": beef, "unit": "kg"},
                {"item_id": k["bun"], "quantity": buns, "unit": "pcs"},
            ],
        },
    )


def test_full_transfer_moves_stock_at_cost(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, other = _two_branches(api)
    res = _ship(api, k, other)
    assert res.status_code == 201, res.text
    t = res.json()
    assert t["status"] == "shipped"
    # In transit: gone from source, not yet at destination
    assert stock_of(api, k["branch"], k["beef"]) == 8000
    assert api.get(f"/branches/{other}/stock").json()[0]["quantity"] is None
    res = api.post(f"/transfers/{t['id']}/receive", {})
    assert res.status_code == 200 and res.json()["status"] == "received"
    assert stock_of(api, other, k["beef"]) == 2000
    assert stock_of(api, other, k["bun"]) == 6
    dest = {r["sku"]: r for r in api.get(f"/branches/{other}/stock").json()}
    assert D(dest["BEEF"]["avg_cost"]) == D("0.4") and D(dest["BUN"]["avg_cost"]) == 5
    assert api.post(f"/transfers/{t['id']}/receive", {}).status_code == 409
    assert api.post(f"/transfers/{t['id']}/cancel", {"reason": "x"}).status_code == 409
    # Tenant-wide value is conserved: 10 000*0.4 + 30*5
    total = session.scalar(select(func.sum(StockBalance.quantity * StockBalance.avg_cost)))
    assert total == D("4150")


def test_partial_receipt_records_costed_transit_loss(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, other = _two_branches(api)
    t = _ship(api, k, other).json()
    res = api.post(
        f"/transfers/{t['id']}/receive",
        {
            "lines": [
                {"item_id": k["beef"], "quantity": "1.5", "unit": "kg"},
                {"item_id": k["bun"], "quantity": "6", "unit": "pcs"},
            ]
        },
    )
    assert res.status_code == 200, res.text
    assert stock_of(api, other, k["beef"]) == 1500
    loss = session.scalars(
        select(StockMovement).where(StockMovement.waste_reason == "transit_loss")
    ).one()
    assert (loss.quantity_base, loss.total_cost) == (D("-500"), D("-200.00"))
    now = datetime.now(UTC)
    period = {
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }
    var = {
        x["sku"]: x
        for x in api.get(f"/branches/{other}/reports/variance", params=period).json()["lines"]
    }
    assert var["BEEF"]["waste_by_reason"] == {"transit_loss": "500.0000"}
    # transfers themselves are not usage
    assert D(var["BEEF"]["theoretical_qty"]) == 0


def test_receive_validation(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, other = _two_branches(api)
    t = _ship(api, k, other).json()
    tid = t["id"]
    more = {
        "lines": [
            {"item_id": k["beef"], "quantity": "3", "unit": "kg"},
            {"item_id": k["bun"], "quantity": "6", "unit": "pcs"},
        ]
    }
    assert api.post(f"/transfers/{tid}/receive", more).status_code == 422
    missing = {"lines": [{"item_id": k["beef"], "quantity": "2", "unit": "kg"}]}
    assert api.post(f"/transfers/{tid}/receive", missing).status_code == 422
    stranger = api.post("/items", {"sku": "X", "name": "x", "base_unit": "g"}).json()["id"]
    alien = {
        "lines": [
            {"item_id": stranger, "quantity": "1", "unit": "g"},
            {"item_id": k["beef"], "quantity": "2", "unit": "kg"},
            {"item_id": k["bun"], "quantity": "6", "unit": "pcs"},
        ]
    }
    assert api.post(f"/transfers/{tid}/receive", alien).status_code == 422
    assert stock_of(api, k["branch"], k["beef"]) == 8000  # nothing half-applied


def test_cancel_returns_stock_to_source(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, other = _two_branches(api)
    t = _ship(api, k, other).json()
    res = api.post(f"/transfers/{t['id']}/cancel", {"reason": "truck broke down"})
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert stock_of(api, k["branch"], k["beef"]) == 10000
    assert stock_of(api, k["branch"], k["bun"]) == 30
    assert api.post(f"/transfers/{t['id']}/receive", {}).status_code == 409


def test_cannot_ship_more_than_on_hand_or_to_self(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, other = _two_branches(api)
    res = _ship(api, k, other, beef="11")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "insufficient_stock"
    assert stock_of(api, k["branch"], k["bun"]) == 30  # rolled back as a whole
    assert _ship(api, k, k["branch"]).status_code == 422
    assert stock_of(api, k["branch"], k["beef"]) == 10000


def test_transfer_scope(client, session: Session) -> None:
    a = new_tenant(session, client, "A")
    k, other = _two_branches(a)
    b = new_tenant(session, client, "B")
    b_branch = b.post("/branches", {"name": "B1"}).json()["id"]
    # A cannot ship into B's branch, B cannot see or receive A's transfer
    assert _ship(a, k, b_branch).status_code == 404
    t = _ship(a, k, other).json()
    assert b.get(f"/transfers/{t['id']}").status_code == 404
    assert b.post(f"/transfers/{t['id']}/receive", {}).status_code == 404
    # A purchasing user of the source branch only cannot receive at the destination
    a.post(
        "/users",
        {
            "email": "p@example.com",
            "password": PASSWORD,
            "role": "purchasing",
            "branch_ids": [k["branch"]],
        },
    )
    p = Api(client, "p@example.com")
    assert p.get(f"/transfers/{t['id']}").status_code == 200
    assert p.post(f"/transfers/{t['id']}/receive", {}).status_code == 404
    # kitchen staff cannot ship
    a.post(
        "/users",
        {
            "email": "c@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    )
    assert _ship(Api(client, "c@example.com"), k, other).status_code == 403
    listed = a.get(f"/branches/{other}/transfers", params={"status": "shipped"}).json()
    assert [x["id"] for x in listed] == [t["id"]]
