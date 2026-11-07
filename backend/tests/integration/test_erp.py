"""Purchasing, daily sales, expenses, staff and profit and loss, and how they feed the count."""

from decimal import Decimal

from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_branch

D = Decimal
P = "2026-08"


def _items(api: Api) -> dict[str, dict]:  # type: ignore[type-arg]
    return {i["name"]: i for i in api.get("/count-items").json()}


def _line(sheet: dict, name: str) -> dict:  # type: ignore[type-arg]
    return next(ln for g in sheet["groups"] for ln in g["lines"] if ln["name"] == name)


def _sheet(api: Api, branch: str) -> dict:  # type: ignore[type-arg]
    return api.get(f"/branches/{branch}/monthly-counts/{P}").json()


def test_goods_receipt_fills_incoming_and_prices(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    items = _items(api)
    sup = api.post("/suppliers", {"name": "Taff Merkez", "phone": "0212"}).json()["id"]
    assert api.post("/suppliers", {"name": "Taff Merkez"}).status_code == 409
    res = api.post(
        f"/branches/{branch}/receipts",
        {
            "date": "2026-08-05",
            "supplier_id": sup,
            "document_no": "IRS-1",
            "lines": [
                {"item_id": items["DANA KIYMA"]["id"], "quantity": "20", "unit_price": "970"},
                {"item_id": items["PATATES"]["id"], "quantity": "50"},
            ],
        },
    )
    assert res.status_code == 201, res.text
    receipt = res.json()["id"]
    api.post(
        f"/branches/{branch}/receipts",
        {
            "date": "2026-08-20",
            "lines": [
                {"item_id": items["DANA KIYMA"]["id"], "quantity": "5", "unit_price": "1000"}
            ],
        },
    )
    sheet = _sheet(api, branch)
    assert D(str(_line(sheet, "DANA KIYMA")["incoming"])) == D("25")
    assert D(str(_line(sheet, "PATATES")["incoming"])) == D("50")
    # the latest purchase price becomes the item's price
    assert D(str(_items(api)["DANA KIYMA"]["unit_price"])) == D("1000")
    rep = api.get(f"/branches/{branch}/receipts?period={P}").json()
    assert [r["document_no"] for r in rep["receipts"]] == [None, "IRS-1"]
    assert D(str(rep["total"])) == D("24400")  # 20×970 + 5×1000
    assert {(b["supplier"], D(str(b["total"]))) for b in rep["by_supplier"]} == {
        ("Taff Merkez", D("19400")),
        ("—", D("5000")),
    }
    # deleting a receipt takes its quantities back out of 'Gelen'
    assert api.client.delete(f"/api/v1/receipts/{receipt}", headers=api.headers).status_code == 204
    assert D(str(_line(_sheet(api, branch), "DANA KIYMA")["incoming"])) == D("5")
    # a closed month does not accept deliveries dated in it
    lines = [{"item_id": i["id"], "counted": "0"} for i in _items(api).values()]
    api.put(f"/branches/{branch}/monthly-counts/{P}", {"counted_by_name": "Ali", "lines": lines})
    assert api.post(f"/branches/{branch}/monthly-counts/{P}/close").status_code == 200
    res = api.post(
        f"/branches/{branch}/receipts",
        {"date": "2026-08-28", "lines": [{"item_id": items["PATATES"]["id"], "quantity": "1"}]},
    )
    assert res.status_code == 409 and "kapatılmış" in res.json()["detail"]["message"]


def test_daily_sales_drive_the_month_and_lock_manual_sales(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    api.post("/menu-products/load-defaults?add_missing_items=true")
    prods = {p["name"]: p for p in api.get("/menu-products").json()}
    youki, nash = prods["YOUKI"]["id"], prods["NASH"]["id"]
    day = api.get(f"/branches/{branch}/sales/day?date=2026-08-01").json()
    assert next(p for p in day["products"] if p["name"] == "YOUKI")["unit_price"] in (
        "610.00",
        610,
        "610",
    )
    api.client.put(
        f"/api/v1/branches/{branch}/sales/day?date=2026-08-01",
        json={
            "entries": [
                {"product_id": youki, "quantity": "40"},
                {"product_id": nash, "quantity": "10"},
            ]
        },
        headers=api.headers,
    )
    r = api.client.put(
        f"/api/v1/branches/{branch}/sales/day?date=2026-08-02",
        json={"entries": [{"product_id": youki, "quantity": "60", "unit_price": "600"}]},
        headers=api.headers,
    )
    assert r.status_code == 200, r.text
    sheet = _sheet(api, branch)
    assert sheet["sales_from_daily"] is True
    assert {s["name"]: D(str(s["quantity"])) for s in sheet["sales"] if D(str(s["quantity"]))} == {
        "YOUKI": D("100"),
        "NASH": D("10"),
    }
    # 100 Youki × 75 g Youki sauce = 7.5 kg taken from the count automatically
    assert D(str(_line(sheet, "YOUKİ SOS")["recipe_used"])) == D("7.5")
    rep = api.get(f"/branches/{branch}/sales?period={P}").json()
    # 40×610 + 10×450 + 60×600
    assert D(str(rep["revenue"])) == D("64900") and D(str(rep["quantity"])) == D("110")
    # manual menu sales on the sheet would double count -> refused
    res = api.client.put(
        f"/api/v1/branches/{branch}/monthly-counts/{P}",
        json={"sales": [{"product_id": youki, "quantity": "1"}]},
        headers=api.headers,
    )
    assert res.status_code == 409 and "günlük" in res.json()["detail"]["message"]
    # setting a day back to 0 removes it from the month
    api.client.put(
        f"/api/v1/branches/{branch}/sales/day?date=2026-08-02",
        json={"entries": [{"product_id": youki, "quantity": "0"}]},
        headers=api.headers,
    )
    assert D(str(_line(_sheet(api, branch), "YOUKİ SOS")["recipe_used"])) == D("3")


def test_expenses_staff_and_profit_loss(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    api.post("/menu-products/load-defaults?add_missing_items=true")
    api.post("/count-items/apply-supplier-data")
    youki = next(p["id"] for p in api.get("/menu-products").json() if p["name"] == "YOUKI")
    api.client.put(
        f"/api/v1/branches/{branch}/sales/day?date=2026-08-10",
        json={"entries": [{"product_id": youki, "quantity": "100"}]},
        headers=api.headers,
    )
    for cat, amount in (("kira", "30000"), ("elektrik", "4500.50")):
        res = api.post(
            f"/branches/{branch}/expenses",
            {"date": "2026-08-01", "category": cat, "amount": amount},
        )
        assert res.status_code == 201, res.text
    assert (
        api.post(
            f"/branches/{branch}/expenses", {"date": "2026-08-01", "category": "x", "amount": "1"}
        ).status_code
        == 422
    )
    ali = api.post(
        f"/branches/{branch}/employees",
        {"name": "Ali", "title": "Usta", "pay_type": "monthly", "wage": "40000"},
    ).json()["id"]
    veli = api.post(
        f"/branches/{branch}/employees", {"name": "Veli", "pay_type": "hourly", "wage": "150"}
    ).json()["id"]
    r = api.post(
        f"/employees/{veli}/shifts", {"date": "2026-08-03", "start": "18:00", "end": "02:30"}
    )
    assert r.status_code == 201 and D(str(r.json()["hours"])) == D("8.5")  # past midnight
    api.post(f"/employees/{veli}/shifts", {"date": "2026-08-04", "hours": "6"})
    staff = api.get(f"/branches/{branch}/staff?period={P}").json()
    assert D(str(staff["hours"])) == D("14.5")
    assert D(str(staff["labor_cost"])) == D("42175")  # 40000 + 14.5 × 150
    assert len(staff["shifts"]) == 2 and ali

    pl = api.get(f"/branches/{branch}/profit-loss?period={P}").json()
    assert D(str(pl["revenue"])) == D("61000")  # 100 × 610
    assert pl["material_basis"].startswith("reçeteye")
    # Youki recipe cost per piece with supplier prices: kıyma 145.50 + cheddar 14.43
    # + trüflü mayonez 17.62 + Kühne 4 slices + Youki sos 19.36 + ekmek 16.50
    assert D(str(pl["material_cost"])) > D("20000")
    assert D(str(pl["expense_total"])) == D("34500.50")
    assert D(str(pl["labor_cost"])) == D("42175")
    expected_net = D(str(pl["revenue"])) - D(str(pl["material_cost"])) - D("34500.50") - D("42175")
    assert D(str(pl["net_profit"])) == expected_net


def test_erp_permissions_and_isolation(client, session: Session) -> None:
    owner = new_tenant(session, client, "A")
    branch = setup_branch(owner)
    items = _items(owner)
    for email, role in (("c@example.com", "counter"), ("v@example.com", "viewer")):
        owner.post(
            "/users", {"email": email, "password": PASSWORD, "role": role, "branch_ids": [branch]}
        )
    counter, viewer = Api(client, "c@example.com"), Api(client, "v@example.com")
    line = {"item_id": items["PATATES"]["id"], "quantity": "3"}
    # counters receive deliveries but see no money
    assert (
        counter.post(
            f"/branches/{branch}/receipts", {"date": "2026-08-01", "lines": [line]}
        ).status_code
        == 201
    )
    assert counter.get(f"/branches/{branch}/profit-loss?period={P}").status_code == 403
    assert (
        counter.post(
            f"/branches/{branch}/expenses",
            {"date": "2026-08-01", "category": "kira", "amount": "1"},
        ).status_code
        == 403
    )
    assert counter.get(f"/branches/{branch}/staff?period={P}").status_code == 403
    # viewers read the money but change nothing
    assert viewer.get(f"/branches/{branch}/profit-loss?period={P}").status_code == 200
    assert viewer.get(f"/branches/{branch}/receipts?period={P}").status_code == 200
    assert (
        viewer.post(
            f"/branches/{branch}/receipts", {"date": "2026-08-01", "lines": [line]}
        ).status_code
        == 403
    )
    # another business can neither read nor use these ids
    other = new_tenant(session, client, "B")
    ob = other.post("/branches", {"name": "B"}).json()["id"]
    assert other.get(f"/branches/{branch}/profit-loss?period={P}").status_code == 404
    assert (
        other.post(f"/branches/{ob}/receipts", {"date": "2026-08-01", "lines": [line]}).status_code
        == 404
    )
    receipt = owner.get(f"/branches/{branch}/receipts?period={P}").json()["receipts"][0]["id"]
    assert (
        other.client.delete(f"/api/v1/receipts/{receipt}", headers=other.headers).status_code == 404
    )
    # the backup carries the new modules
    data = owner.get("/backup").json()
    assert len(data["tables"]["purchase_receipts"]) == 1
    assert len(data["tables"]["purchase_receipt_lines"]) == 1
