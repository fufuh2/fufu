from decimal import Decimal as D

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen, stock_of


def _setup(api: Api) -> tuple[dict[str, str], str]:
    k = setup_kitchen(api)  # beef 10 000 g @0.40
    sup = api.post("/suppliers", {"name": "Et A.S.", "email": "satis@et-example.com"}).json()["id"]
    return k, sup


def _po(api: Api, k: dict[str, str], sup: str, kg: str = "10", price: str = "400"):  # type: ignore[no-untyped-def]
    return api.post(
        f"/branches/{k['branch']}/purchase-orders",
        {
            "supplier_id": sup,
            "lines": [{"item_id": k["beef"], "quantity": kg, "unit": "kg", "unit_price": price}],
        },
    )


def test_po_lifecycle_with_partial_receipts_and_invoice(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, sup = _setup(api)
    po = _po(api, k, sup).json()
    assert (po["status"], po["total"]) == ("draft", "4000.00")
    # cannot receive before approval
    assert (
        api.post(
            f"/purchase-orders/{po['id']}/receipts",
            {"lines": [{"item_id": k["beef"], "quantity": "1", "unit": "kg"}]},
        ).status_code
        == 409
    )
    assert api.post(f"/purchase-orders/{po['id']}/submit").json()["status"] == "pending_approval"
    assert api.post(f"/purchase-orders/{po['id']}/approve").json()["status"] == "approved"
    # partial: 4 kg at the ordered price
    r1 = api.post(
        f"/purchase-orders/{po['id']}/receipts",
        {"lines": [{"item_id": k["beef"], "quantity": "4", "unit": "kg"}]},
    )
    assert r1.status_code == 201, r1.text
    assert (r1.json()["status"], r1.json()["received_value"], r1.json()["price_variances"]) == (
        "partially_received",
        "1600.00",
        [],
    )
    assert stock_of(api, k["branch"], k["beef"]) == 14000
    # over the open quantity is refused
    assert (
        api.post(
            f"/purchase-orders/{po['id']}/receipts",
            {"lines": [{"item_id": k["beef"], "quantity": "7", "unit": "kg"}]},
        ).status_code
        == 422
    )
    # rest arrives 10 % more expensive -> price variance alert
    r2 = api.post(
        f"/purchase-orders/{po['id']}/receipts",
        {"lines": [{"item_id": k["beef"], "quantity": "6000", "unit": "g", "unit_price": "0.44"}]},
    ).json()
    assert r2["status"] == "received"
    assert r2["received_value"] == "2640.00"
    assert r2["price_variances"][0]["difference_pct"] == "10.00"
    # average: (10000*0.40 + 4000*0.40 + 6000*0.44) / 20000 = 0.412
    beef = next(x for x in api.get(f"/branches/{k['branch']}/stock").json() if x["sku"] == "BEEF")
    assert D(beef["avg_cost"]) == D("0.412")
    # invoice: received value 4240.00
    inv = api.post(
        f"/purchase-orders/{po['id']}/invoices", {"invoice_no": "F-1", "amount": "4300"}
    ).json()
    assert (inv["status"], inv["difference"]) == ("mismatch", "60.00")
    assert (
        api.post(
            f"/purchase-orders/{po['id']}/invoices", {"invoice_no": "F-1", "amount": "1"}
        ).status_code
        == 409
    )
    n = session.execute(
        text("SELECT count(*) FROM audit_events WHERE action='purchase.price_variance'")
    ).scalar()
    assert n == 1
    hist = api.get(f"/suppliers/{sup}/prices", params={"item_id": k["beef"]}).json()
    assert [h["unit_price"] for h in hist] == ["0.400000", "0.440000"]


def test_close_partially_received(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, sup = _setup(api)
    po = _po(api, k, sup, kg="5", price="100").json()
    api.post(f"/purchase-orders/{po['id']}/submit")
    api.post(f"/purchase-orders/{po['id']}/approve")
    api.post(
        f"/purchase-orders/{po['id']}/receipts",
        {"lines": [{"item_id": k["beef"], "quantity": "1", "unit": "kg"}]},
    )
    assert api.post(f"/purchase-orders/{po['id']}/cancel", {"reason": "x"}).status_code == 409
    res = api.post(f"/purchase-orders/{po['id']}/close", {"reason": "supplier out of stock"})
    assert res.json()["status"] == "closed"
    inv = api.post(
        f"/purchase-orders/{po['id']}/invoices", {"invoice_no": "F-9", "amount": "100"}
    ).json()
    assert inv["status"] == "matched"


def test_four_eyes_above_limit(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k, sup = _setup(owner)
    owner.post(
        "/users",
        {
            "email": "buyer@example.com",
            "password": PASSWORD,
            "role": "branch_manager",
            "branch_ids": [k["branch"]],
        },
    )
    buyer = Api(client, "buyer@example.com")
    big = _po(buyer, k, sup, kg="100", price="400").json()  # 40 000 > 10 000 limit
    buyer.post(f"/purchase-orders/{big['id']}/submit")
    assert buyer.post(f"/purchase-orders/{big['id']}/approve").status_code == 403
    assert owner.post(f"/purchase-orders/{big['id']}/approve").json()["approved_by"] is not None
    small = _po(buyer, k, sup, kg="1", price="400").json()
    buyer.post(f"/purchase-orders/{small['id']}/submit")
    assert buyer.post(f"/purchase-orders/{small['id']}/approve").status_code == 200
    # purchasing role can create but not approve
    owner.post(
        "/users",
        {
            "email": "pur@example.com",
            "password": PASSWORD,
            "role": "purchasing",
            "branch_ids": [k["branch"]],
        },
    )
    pur = Api(client, "pur@example.com")
    p = _po(pur, k, sup, kg="1").json()
    pur.post(f"/purchase-orders/{p['id']}/submit")
    assert pur.post(f"/purchase-orders/{p['id']}/approve").status_code == 403


def test_suggestions_include_open_orders(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, sup = _setup(api)  # beef min 2000
    api.client.patch(
        f"/api/v1/items/{k['beef']}", json={"target_stock": "12000"}, headers=api.headers
    )
    api.post(
        f"/branches/{k['branch']}/waste",
        {"item_id": k["beef"], "quantity": "9", "unit": "kg", "reason": "spoilage_expiry"},
    )
    sug = {x["sku"]: x for x in api.get(f"/branches/{k['branch']}/purchase-suggestions").json()}
    assert D(sug["BEEF"]["suggested_quantity"]) == 11000  # 12000 - 1000
    po = _po(api, k, sup, kg="5").json()
    api.post(f"/purchase-orders/{po['id']}/submit")
    sug = {x["sku"]: x for x in api.get(f"/branches/{k['branch']}/purchase-suggestions").json()}
    assert "BEEF" not in sug  # 1000 on hand + 5000 open >= 2000 minimum
    # target below minimum is rejected
    res = api.client.patch(
        f"/api/v1/items/{k['beef']}", json={"target_stock": "100"}, headers=api.headers
    )
    assert res.status_code == 422


def test_purchasing_scope(client, session: Session) -> None:
    a = new_tenant(session, client, "A")
    k, sup = _setup(a)
    po = _po(a, k, sup).json()
    b = new_tenant(session, client, "B")
    assert b.get(f"/purchase-orders/{po['id']}").status_code == 404
    assert b.get("/suppliers").json() == []
    bb = b.post("/branches", {"name": "B"}).json()["id"]
    assert (
        b.post(
            f"/branches/{bb}/purchase-orders",
            {
                "supplier_id": sup,
                "lines": [{"item_id": k["beef"], "quantity": "1", "unit": "kg", "unit_price": "1"}],
            },
        ).status_code
        == 404
    )
    a.post(
        "/users",
        {
            "email": "k@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    )
    assert _po(Api(client, "k@example.com"), k, sup).status_code == 403


def test_quote_price_is_stored_per_base_unit(client, session: Session) -> None:
    api = new_tenant(session, client)
    k, sup = _setup(api)
    res = api.post(
        f"/suppliers/{sup}/prices", {"item_id": k["bun"], "unit_price": "36", "unit": "pack"}
    )
    assert res.status_code == 201 and res.json()["unit_price"] == "6.000000"  # 36 / 6 pcs
    assert (
        api.client.patch(
            f"/api/v1/suppliers/{sup}", json={"is_active": False}, headers=api.headers
        ).status_code
        == 200
    )
    assert _po(api, k, sup).status_code == 404
