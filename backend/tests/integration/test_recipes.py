"""Menu products × recipe -> 'Satıştan (reçete)' usage, e.g. sauces used in several burgers."""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.monthly import compute_line, to_item_units
from tests.conftest import PASSWORD, Api, new_tenant, setup_branch

P = "2026-08"
D = Decimal


def _items(api: Api) -> dict[str, str]:
    return {i["name"]: i["id"] for i in api.get("/count-items").json()}


def _line(sheet: dict, name: str) -> dict:  # type: ignore[type-arg]
    return next(ln for g in sheet["groups"] for ln in g["lines"] if ln["name"] == name)


def _product(api: Api, name: str, lines: list[dict]) -> str:  # type: ignore[type-arg]
    pid = api.post("/menu-products", {"name": name}).json()["id"]
    res = api.put(f"/menu-products/{pid}/recipe", {"lines": lines})
    assert res.status_code == 200, res.text
    return pid


def test_unit_conversion() -> None:
    assert to_item_units(D("20"), "GR", "KG") == D("0.02")
    assert to_item_units(D("30"), "ML", "LT") == D("0.03")
    assert to_item_units(D("1"), "ADET", "ADET") == D("1")
    assert to_item_units(D("20"), "GR", "ADET") is None
    # a 9.5 kg bucket counted in pieces
    assert to_item_units(D("19"), "GR", "ADET", D("9.5"), "KG") == D("0.002")
    assert to_item_units(D("2"), "ADET", "KOLİ", D("12"), "ADET") == D("2") / D("12")
    assert to_item_units(D("1"), "ML", "ADET", D("1"), "KG") is None
    r = compute_line(D("10"), D("5"), D("1"), D("9"), None, D("0.5"), D("4.4"))
    assert r.expected == D("9.1") and r.difference == D("-0.1")


def test_sauce_used_by_two_burgers(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    items = _items(api)
    api.patch(f"/count-items/{items['YOUKİ SOS']}", {"unit_price": "300"})
    klasik = _product(
        api,
        "Klasik Burger",
        [
            {"item_id": items["YOUKİ SOS"], "quantity": "20", "unit": "GR"},
            {"item_id": items["DANA KIYMA"], "quantity": "150", "unit": "GR"},
            {"item_id": items["HAMBURGER EKMEĞİ"], "quantity": "1", "unit": "ADET"},
        ],
    )
    smash = _product(
        api,
        "Smash Burger",
        [
            {"item_id": items["YOUKİ SOS"], "quantity": "15", "unit": "GR"},
            {"item_id": items["HAMBURGER EKMEĞİ"], "quantity": "1", "unit": "ADET"},
        ],
    )
    # 120 × 20 g + 80 × 15 g = 3.6 kg sauce; 200 buns; 120 × 150 g = 18 kg beef
    res = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {
            "sales": [
                {"product_id": klasik, "quantity": "120"},
                {"product_id": smash, "quantity": "80"},
            ],
            "lines": [
                {
                    "item_id": items["YOUKİ SOS"],
                    "opening": "2",
                    "incoming": "5",
                    "waste": "0.2",
                    "counted": "3",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    sheet = res.json()
    sos = _line(sheet, "YOUKİ SOS")
    # expected 2 + 5 - 3.6 - 0 - 0.2 = 3.2; counted 3 -> 0.2 kg eksik = -60 TL
    assert D(str(sos["recipe_used"])) == D("3.6")
    assert D(str(sos["expected"])) == D("3.2")
    assert (D(str(sos["difference"])), sos["status"]) == (D("-0.2"), "eksik")
    assert D(str(sos["difference_value"])) == D("-60")
    assert D(str(_line(sheet, "HAMBURGER EKMEĞİ")["recipe_used"])) == D("200")
    assert D(str(_line(sheet, "DANA KIYMA")["recipe_used"])) == D("18")
    assert {s["name"]: D(str(s["quantity"])) for s in sheet["sales"]} == {
        "Klasik Burger": D("120"),
        "Smash Burger": D("80"),
    }
    assert sheet["recipes"][items["YOUKİ SOS"]]

    # recipe change recomputes the draft ...
    api.put(
        f"/menu-products/{smash}/recipe",
        {"lines": [{"item_id": items["YOUKİ SOS"], "quantity": "25", "unit": "GR"}]},
    )
    sheet = api.get(f"/branches/{branch}/monthly-counts/{P}").json()
    assert D(str(_line(sheet, "YOUKİ SOS")["recipe_used"])) == D("4.4")  # 2.4 + 2.0
    assert D(str(_line(sheet, "HAMBURGER EKMEĞİ")["recipe_used"])) == D("120")

    # ... but a closed month keeps the usage it was closed with
    lines = [{"item_id": i, "counted": "0"} for n, i in items.items() if n != "YOUKİ SOS"]
    api.put(f"/branches/{branch}/monthly-counts/{P}", {"counted_by_name": "Ali", "lines": lines})
    assert api.post(f"/branches/{branch}/monthly-counts/{P}/close").status_code == 200
    api.put(
        f"/menu-products/{smash}/recipe",
        {"lines": [{"item_id": items["YOUKİ SOS"], "quantity": "50", "unit": "GR"}]},
    )
    sheet = api.get(f"/branches/{branch}/monthly-counts/{P}").json()
    assert D(str(_line(sheet, "YOUKİ SOS")["recipe_used"])) == D("4.4")
    assert sheet["recipes"] is None
    # sales of a closed month cannot change
    assert (
        api.put(
            f"/branches/{branch}/monthly-counts/{P}",
            {"sales": [{"product_id": klasik, "quantity": "1"}]},
        ).status_code
        == 409
    )
    h = api.get(f"/branches/{branch}/count-items/{items['YOUKİ SOS']}/history").json()
    assert D(str(h["months"][0]["recipe_used"])) == D("4.4")


def test_package_items_need_content(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    items = _items(api)
    pid = api.post("/menu-products", {"name": "Patates Menü"}).json()["id"]
    line = {"item_id": items["KOVA KETÇAP"], "quantity": "19", "unit": "GR"}
    # saved, but not deducted until the bucket's content is known; the sheet says so
    res = api.put(f"/menu-products/{pid}/recipe", {"lines": [line]})
    assert res.status_code == 200 and res.json()["recipe"][0]["per_item_unit"] is None
    sheet = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {"sales": [{"product_id": pid, "quantity": "1000"}]},
    ).json()
    assert D(str(_line(sheet, "KOVA KETÇAP")["recipe_used"])) == D("0")
    assert [
        (w["item_name"], w["recipe_units"], w["products"]) for w in sheet["recipe_warnings"]
    ] == [("KOVA KETÇAP", ["GR"], ["Patates Menü"])]
    api.patch(f"/count-items/{items['KOVA KETÇAP']}", {"content_qty": "9.5", "content_unit": "KG"})
    sheet = api.get(f"/branches/{branch}/monthly-counts/{P}").json()
    # 1000 × 19 g = 19 kg = 2 buckets of 9.5 kg
    assert D(str(_line(sheet, "KOVA KETÇAP")["recipe_used"])) == D("2")
    assert sheet["recipe_warnings"] == []
    # half-filled content is rejected
    res = api.patch(f"/count-items/{items['KOVA KETÇAP']}", {"content_unit": None})
    assert res.status_code == 422
    # sales back to 0 removes the usage
    sheet = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {"sales": [{"product_id": pid, "quantity": "0"}]},
    ).json()
    assert D(str(_line(sheet, "KOVA KETÇAP")["recipe_used"])) == D("0")


def test_cheddar_slices_through_content(client, session: Session) -> None:
    # cheddar is counted in KG; "1 KG = 50 ADET" lets the recipe say 2 slices
    assert to_item_units(D("2"), "ADET", "KG", D("50"), "ADET") == D("0.04")


def test_menu_boards_loaded(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    res = api.post("/menu-products/load-defaults")
    assert res.status_code == 200, res.text
    r = res.json()
    assert len(r["added_products"]) == 19 and r["added_items"] == []
    missing = {m["name"] for m in r["missing_items"]}
    assert {"ICEBERG", "KAPLAMALI TAVUK", "DYNAMITE SOS", "TAHİN SOS"} <= missing
    assert {w["item_name"] for w in r["unconverted"]} >= {"CHEDAR PEYNİRİ", "KOVA MAYONEZ"}
    products = {p["name"]: p for p in api.get("/menu-products").json()}
    youki = {ln["item_name"]: ln for ln in products["YOUKI"]["recipe"]}
    assert D(str(youki["YOUKİ SOS"]["per_item_unit"])) == D("0.075")
    assert D(str(youki["DANA KIYMA"]["per_item_unit"])) == D("0.15")
    assert "HAMBURGER EKMEĞİ" not in {ln["item_name"] for ln in products["BALANCE"]["recipe"]}
    assert D(
        str(
            next(ln for ln in products["ROYALE"]["recipe"] if ln["item_name"] == "DANA KIYMA")[
                "quantity"
            ]
        )
    ) == D("225")

    # second run with the missing ingredients completes the recipes without duplicates
    before = sum(len(p["recipe"]) for p in products.values())
    r2 = api.post("/menu-products/load-defaults?add_missing_items=true").json()
    assert r2["added_products"] == [] and r2["missing_items"] == []
    assert len(r2["added_items"]) == 18 and "ÖZEL BBQ SOS" in r2["added_items"]
    after = sum(len(p["recipe"]) for p in api.get("/menu-products").json())
    assert after - before == r2["added_lines"] > 0
    assert api.post("/menu-products/load-defaults").json()["added_lines"] == 0

    # YOUKİ SOS: 100 Youki × 75 g + 40 Cheese Tao × 150 g = 13.5 kg
    products = {p["name"]: p["id"] for p in api.get("/menu-products").json()}
    sheet = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {
            "sales": [
                {"product_id": products["YOUKI"], "quantity": "100"},
                {"product_id": products["CHEESE TAO"], "quantity": "40"},
            ]
        },
    ).json()
    assert D(str(_line(sheet, "YOUKİ SOS")["recipe_used"])) == D("13.5")
    assert D(str(_line(sheet, "HAMBURGER EKMEĞİ")["recipe_used"])) == D("140")
    assert D(str(_line(sheet, "KAPLAMALI TAVUK")["recipe_used"])) == D("4.8")


def test_menu_permissions_and_isolation(client, session: Session) -> None:
    owner = new_tenant(session, client, "A")
    branch = setup_branch(owner)
    items = _items(owner)
    pid = _product(
        owner, "Klasik", [{"item_id": items["YOUKİ SOS"], "quantity": "20", "unit": "GR"}]
    )
    owner.post(
        "/users",
        {"email": "c@example.com", "password": PASSWORD, "role": "counter", "branch_ids": [branch]},
    )
    counter = Api(client, "c@example.com")
    assert counter.get("/menu-products").status_code == 200
    assert counter.post("/menu-products", {"name": "X"}).status_code == 403
    assert counter.put(f"/menu-products/{pid}/recipe", {"lines": []}).status_code == 403
    # counters enter the month's sales
    res = counter.put(
        f"/branches/{branch}/monthly-counts/{P}", {"sales": [{"product_id": pid, "quantity": "5"}]}
    )
    assert res.status_code == 200
    other = new_tenant(session, client, "B")
    ob = other.post("/branches", {"name": "B"}).json()["id"]
    assert other.get("/menu-products").json() == []
    assert other.patch(f"/menu-products/{pid}", {"name": "x"}).status_code == 404
    assert (
        other.put(
            f"/branches/{ob}/monthly-counts/{P}", {"sales": [{"product_id": pid, "quantity": "1"}]}
        ).status_code
        == 404
    )
    assert owner.post("/menu-products", {"name": "Klasik"}).status_code == 409
    dup = [
        {"item_id": items["YOUKİ SOS"], "quantity": "1", "unit": "GR"},
        {"item_id": items["YOUKİ SOS"], "quantity": "2", "unit": "GR"},
    ]
    assert owner.put(f"/menu-products/{pid}/recipe", {"lines": dup}).status_code == 422


def test_supplier_prices_and_food_cost(client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
    api.post("/menu-products/load-defaults?add_missing_items=true")
    items = _items(api)
    api.patch(f"/count-items/{items['YOUKİ SOS']}", {"unit_price": "300"})  # user's own price
    r = api.post("/count-items/apply-supplier-data").json()
    applied = {a["item"]: a for a in r["applied"]}
    assert applied["CHEDAR PEYNİRİ"]["changes"] == {
        "unit_price": "505.00",
        "content": "1 KG = 70 ADET",
    }
    assert applied["KOVA KETÇAP"]["changes"]["content"] == "1 ADET = 9 KG"
    assert "YOUKİ SOS" not in applied  # an entered price is kept without overwrite
    assert r["not_in_list"] == [] and r["not_in_count_list"]["rows"]
    by_name = {i["name"]: i for i in api.get("/count-items").json()}
    assert D(str(by_name["YOUKİ SOS"]["unit_price"])) == D("300")
    r = api.post("/count-items/apply-supplier-data?overwrite=true").json()
    assert {a["item"] for a in r["applied"]} == {"YOUKİ SOS"}
    assert api.post("/count-items/apply-supplier-data").json()["applied"] == []

    youki = next(p for p in api.get("/menu-products").json() if p["name"] == "YOUKI")
    costs = {ln["item_name"]: ln["cost"] for ln in youki["recipe"]}
    # 2 slices of 70/kg × 505; 75 g × 234.90; 75 g × 258.10; bun 16.50
    assert D(str(costs["CHEDAR PEYNİRİ"])) == D("14.43")
    assert D(str(costs["TRÜFLÜ MAYONEZ"])) == D("17.62")
    assert D(str(costs["YOUKİ SOS"])) == D("19.36")
    assert D(str(costs["DANA KIYMA"])) == D("145.50")  # 150 g köfte at 970/kg
    assert D(str(costs["KÜHNE ALMAN TURŞU"])) == D("4.32")  # 4 of ~285 slices/kg × 307.94
    assert D(str(youki["cost"])) == D("217.73") and youki["cost_complete"] is True
    assert D(str(youki["price"])) == D("610") and D(str(youki["food_cost_pct"])) == D("35.7")
    pid = youki["id"]
    assert api.patch(f"/menu-products/{pid}", {"price": "650"}).json()["price"] in (
        "650",
        "650.00",
        650,
    )
