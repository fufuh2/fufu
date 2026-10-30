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
    res = api.put(f"/menu-products/{pid}/recipe", {"lines": [line]})
    assert res.status_code == 422 and "paket içeriğini" in res.json()["detail"]["message"]
    api.patch(f"/count-items/{items['KOVA KETÇAP']}", {"content_qty": "9.5", "content_unit": "KG"})
    assert api.put(f"/menu-products/{pid}/recipe", {"lines": [line]}).status_code == 200
    sheet = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {"sales": [{"product_id": pid, "quantity": "1000"}]},
    ).json()
    # 1000 × 19 g = 19 kg = 2 buckets of 9.5 kg
    assert D(str(_line(sheet, "KOVA KETÇAP")["recipe_used"])) == D("2")
    # the content cannot be removed while a recipe needs it
    res = api.patch(
        f"/count-items/{items['KOVA KETÇAP']}", {"content_qty": None, "content_unit": None}
    )
    assert res.status_code == 422 and "Patates Menü" in res.json()["detail"]["message"]
    res = api.patch(f"/count-items/{items['KOVA KETÇAP']}", {"content_qty": "1"})
    assert res.status_code == 200
    res = api.patch(f"/count-items/{items['KOVA KETÇAP']}", {"content_unit": None})
    assert res.status_code == 422
    # sales back to 0 removes the usage
    sheet = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {"sales": [{"product_id": pid, "quantity": "0"}]},
    ).json()
    assert D(str(_line(sheet, "KOVA KETÇAP")["recipe_used"])) == D("0")


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
