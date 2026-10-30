"""Monthly inventory count: the Taff Burger form as a computed, locked monthly process."""

from io import BytesIO

import openpyxl
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant


def _setup(api: Api) -> tuple[str, dict[str, str]]:
    branch = api.post("/branches", {"name": "Taff Burger Merkez"}).json()["id"]
    res = api.post("/count-items/load-defaults")
    assert res.status_code == 200 and res.json() == {"added": 98, "skipped": 0}
    items = {i["name"]: i["id"] for i in api.get("/count-items").json()}
    return branch, items


def _line(sheet: dict, name: str) -> dict:
    return next(ln for g in sheet["groups"] for ln in g["lines"] if ln["name"] == name)


def _put(api: Api, branch: str, period: str, body: dict):  # type: ignore[no-untyped-def]
    return api.client.put(
        f"/api/v1/branches/{branch}/monthly-counts/{period}", json=body, headers=api.headers
    )


def _count_all(api: Api, branch: str, period: str, items: dict[str, str], **override) -> None:  # type: ignore[no-untyped-def]
    lines = [{"item_id": i, "counted": "0"} for n, i in items.items() if n not in override]
    lines += [{"item_id": items[n], **v} for n, v in override.items()]
    res = _put(
        api,
        branch,
        period,
        {"counted_by_name": "Ali Veli", "count_date": "2026-08-31", "lines": lines},
    )
    assert res.status_code == 200, res.text


def test_form_items_loaded_in_groups(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch, items = _setup(api)
    assert len(items) == 98
    sheet = api.get(f"/branches/{branch}/monthly-counts/2026-08").json()
    assert [g["name"] for g in sheet["groups"]] == [
        "Gıda Ürünleri",
        "Ambalaj Ürünleri",
        "Temizlik Malzemeleri",
        "İçecekler",
        "Mutfak Sosları",
        "Personel Yemek Malzemeleri",
    ]
    assert sheet["groups"][0]["lines"][0]["name"] == "DANA KIYMA"
    assert sheet["groups"][0]["lines"][0]["unit"] == "KG"
    assert sheet["totals"]["items"] == 98 and sheet["totals"]["counted"] == 0
    assert api.post("/count-items/load-defaults").json() == {"added": 0, "skipped": 98}


def test_month_computes_and_next_month_carries_over(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch, items = _setup(api)
    kiyma = items["DANA KIYMA"]
    api.client.patch(
        f"/api/v1/count-items/{kiyma}", json={"unit_price": "400"}, headers=api.headers
    )
    # August: first month, opening entered by hand
    res = _put(
        api,
        branch,
        "2026-08",
        {
            "lines": [
                {
                    "item_id": kiyma,
                    "opening": "10",
                    "incoming": "50",
                    "used": "45",
                    "counted": "12",
                },
                {
                    "item_id": items["HAMBURGER EKMEĞİ"],
                    "opening": "100",
                    "incoming": "600",
                    "used": "640",
                    "counted": "62",
                },
            ]
        },
    )
    sheet = res.json()
    k = _line(sheet, "DANA KIYMA")
    assert (k["expected"], k["difference"], k["status"], k["difference_value"]) == (
        "15.000",
        "-3.000",
        "eksik",
        "-1200.00",
    )
    e = _line(sheet, "HAMBURGER EKMEĞİ")
    assert (e["expected"], e["difference"], e["status"]) == ("60.000", "2.000", "fazla")
    assert sheet["totals"]["eksik"] == 1 and sheet["totals"]["fazla"] == 1
    assert sheet["totals"]["eksik_value"] == "-1200.00"
    # cannot close with uncounted items or without the counter's name
    assert api.post(f"/branches/{branch}/monthly-counts/2026-08/close").status_code == 422
    _count_all(
        api,
        branch,
        "2026-08",
        items,
        **{"DANA KIYMA": {"counted": "12"}, "HAMBURGER EKMEĞİ": {"counted": "62"}},
    )
    closed = api.post(f"/branches/{branch}/monthly-counts/2026-08/close")
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    # closed month is read-only
    assert (
        _put(api, branch, "2026-08", {"lines": [{"item_id": kiyma, "counted": "99"}]}).status_code
        == 409
    )
    # price change later does not rewrite August
    api.client.patch(
        f"/api/v1/count-items/{kiyma}", json={"unit_price": "500"}, headers=api.headers
    )
    aug = api.get(f"/branches/{branch}/monthly-counts/2026-08").json()
    assert _line(aug, "DANA KIYMA")["difference_value"] == "-1200.00"
    # September opening = August counted, locked
    sep = api.get(f"/branches/{branch}/monthly-counts/2026-09").json()
    ks = _line(sep, "DANA KIYMA")
    assert (ks["opening"], ks["opening_locked"], sep["opening_from"]) == ("12.000", True, "2026-08")
    bad = _put(api, branch, "2026-09", {"lines": [{"item_id": kiyma, "opening": "20"}]})
    assert bad.status_code == 422
    ok = _put(
        api,
        branch,
        "2026-09",
        {
            "lines": [
                {"item_id": kiyma, "opening": "12", "incoming": "40", "used": "50", "counted": "2"}
            ]
        },
    )
    assert _line(ok.json(), "DANA KIYMA")["status"] == "tamam"  # 12 + 40 - 50 = 2
    # 1.5 kg zayi: expected drops to 0.5, counted 2 -> 1.5 fazla; zayi valued at 500 = 750
    zw = _put(api, branch, "2026-09", {"lines": [{"item_id": kiyma, "waste": "1.5"}]}).json()
    kz = _line(zw, "DANA KIYMA")
    assert (kz["waste"], kz["expected"], kz["difference"], kz["status"], kz["waste_value"]) == (
        "1.500",
        "0.500",
        "1.500",
        "fazla",
        "750.00",
    )
    assert zw["totals"]["waste_lines"] == 1 and zw["totals"]["waste_value"] == "750.00"
    neg = _put(api, branch, "2026-09", {"lines": [{"item_id": kiyma, "waste": "-1"}]})
    assert neg.status_code == 422


def test_reopen_rules(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch, items = _setup(api)
    _count_all(api, branch, "2026-08", items)
    api.post(f"/branches/{branch}/monthly-counts/2026-08/close")
    _count_all(api, branch, "2026-09", items)
    api.post(f"/branches/{branch}/monthly-counts/2026-09/close")
    assert (
        api.post(
            f"/branches/{branch}/monthly-counts/2026-08/reopen", {"reason": "hata"}
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/branches/{branch}/monthly-counts/2026-09/reopen", {"reason": "yanlış giriş"}
        ).status_code
        == 200
    )
    # October's opening is no longer locked while September is open again
    octo = api.get(f"/branches/{branch}/monthly-counts/2026-10").json()
    assert octo["opening_from"] is None and _line(octo, "DANA KIYMA")["opening_locked"] is False
    ev = session.execute(
        text("SELECT data FROM audit_events WHERE action='monthly_count.reopen'")
    ).scalar()
    assert ev["reason"] == "yanlış giriş"


def test_validation(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch, items = _setup(api)
    k = items["DANA KIYMA"]
    assert api.get(f"/branches/{branch}/monthly-counts/2026-13").status_code == 422
    for body in (
        {"item_id": k, "used": "-1"},
        {"item_id": k, "counted": "1.0001"},
        {"item_id": k, "incoming": "abc"},
    ):
        assert _put(api, branch, "2026-08", {"lines": [body]}).status_code == 422
    assert (
        api.post(
            "/count-items", {"group_name": "Gıda", "name": "DANA KIYMA", "unit": "KG"}
        ).status_code
        == 409
    )
    assert (
        api.post("/count-items", {"group_name": "Gıda", "name": "X", "unit": "TON"}).status_code
        == 422
    )
    new = api.post(
        "/count-items",
        {
            "group_name": "Gıda Ürünleri",
            "name": "CHEDDAR DİLİM",
            "unit": "PKT",
            "unit_price": "85.50",
        },
    ).json()
    sheet = api.get(f"/branches/{branch}/monthly-counts/2026-08").json()
    assert _line(sheet, "CHEDDAR DİLİM")["unit_price"] == "85.50"  # new item joins the draft
    api.client.patch(
        f"/api/v1/count-items/{new['id']}", json={"is_active": False}, headers=api.headers
    )
    sheet = api.get(f"/branches/{branch}/monthly-counts/2026-08").json()
    assert all(ln["name"] != "CHEDDAR DİLİM" for g in sheet["groups"] for ln in g["lines"])


def test_excel_export(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch, items = _setup(api)
    api.client.patch(
        f"/api/v1/count-items/{items['PATATES']}", json={"unit_price": "30"}, headers=api.headers
    )
    _put(
        api,
        branch,
        "2026-08",
        {
            "counted_by_name": "Ayşe",
            "lines": [
                {
                    "item_id": items["PATATES"],
                    "opening": "20",
                    "incoming": "100",
                    "used": "90",
                    "waste": "3",
                    "counted": "25",
                }
            ],
        },
    )
    res = api.get(f"/branches/{branch}/monthly-counts/2026-08/export.xlsx")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/vnd.openxmlformats")
    ws = openpyxl.load_workbook(BytesIO(res.content)).active
    rows = {ws.cell(r, 2).value: r for r in range(1, ws.max_row + 1)}
    r = rows["PATATES"]
    header = [ws.cell(rows["Malzeme"], c).value for c in range(1, 15)]
    assert header[6] == "Zayi" and header[13] == "Zayi tutarı (₺)"
    # devir 20 + gelen 100 - satılan 90 - zayi 3 = 27; sayılan 25 -> 2 eksik
    assert [ws.cell(r, c).value for c in (3, 4, 5, 6, 7, 9)] == ["KG", 20, 100, 90, 3, 25]
    assert ws.cell(r, 8).value == f"=D{r}+E{r}-F{r}-G{r}"
    assert ws.cell(r, 10).value == f'=IF(I{r}="","",I{r}-H{r})'
    assert ws.cell(r, 11).value == "EKSİK"
    assert ws.cell(r, 14).value == f'=IF(L{r}="","",G{r}*L{r})'
    assert "TAFF BURGER MERKEZ" in ws["A1"].value


def test_permissions_and_isolation(client, session: Session) -> None:
    owner = new_tenant(session, client, "A")
    branch, items = _setup(owner)
    owner.post(
        "/users",
        {
            "email": "cook@example.com",
            "password": PASSWORD,
            "role": "shift_lead",
            "branch_ids": [branch],
        },
    )
    lead = Api(client, "cook@example.com")
    # shift lead may count but not close; may not change product prices
    assert (
        _put(
            lead, branch, "2026-08", {"lines": [{"item_id": items["PATATES"], "counted": "5"}]}
        ).status_code
        == 200
    )
    assert lead.post(f"/branches/{branch}/monthly-counts/2026-08/close").status_code == 403
    assert (
        lead.client.patch(
            f"/api/v1/count-items/{items['PATATES']}",
            json={"unit_price": "1"},
            headers=lead.headers,
        ).status_code
        == 403
    )
    other = new_tenant(session, client, "B")
    assert other.get(f"/branches/{branch}/monthly-counts/2026-08").status_code == 404
    assert other.get("/count-items").json() == []
    ob = other.post("/branches", {"name": "B"}).json()["id"]
    assert _put(
        other, ob, "2026-08", {"lines": [{"item_id": items["PATATES"], "counted": "1"}]}
    ).status_code in (404, 422)
