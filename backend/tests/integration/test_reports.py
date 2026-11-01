"""Dashboard, month list with totals, product history, branch overview, users, audit log."""

from decimal import Decimal

from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_branch


def _items(api: Api) -> dict[str, str]:
    return {i["name"]: i["id"] for i in api.get("/count-items").json()}


def _close(api: Api, branch: str, period: str, items: dict[str, str], lines: dict) -> None:  # type: ignore[type-arg]
    body = [{"item_id": i, "counted": "0"} for n, i in items.items() if n not in lines]
    body += [{"item_id": items[n], **v} for n, v in lines.items()]
    res = api.put(
        f"/branches/{branch}/monthly-counts/{period}",
        {"counted_by_name": "Ali", "lines": body},
    )
    assert res.status_code == 200, res.text
    assert api.post(f"/branches/{branch}/monthly-counts/{period}/close").status_code == 200


def test_dashboard_trend_history_and_overview(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    items = _items(api)
    api.patch(f"/count-items/{items['DANA KIYMA']}", {"unit_price": "400"})
    api.patch(f"/count-items/{items['PATATES']}", {"unit_price": "50"})
    # August: kıyma 10 + 50 - 45 - zayi 2 = 13, sayılan 12 -> 1 kg eksik (-400 TL), zayi 800 TL
    _close(
        api,
        branch,
        "2026-08",
        items,
        {
            "DANA KIYMA": {
                "opening": "10",
                "incoming": "50",
                "used": "45",
                "waste": "2",
                "counted": "12",
            },
            "PATATES": {"opening": "5", "incoming": "20", "used": "20", "counted": "3"},
        },
    )
    # September: opening comes from August; 12 + 30 - 35 = 7, sayılan 7 -> tamam
    api.put(
        f"/branches/{branch}/monthly-counts/2026-09",
        {
            "lines": [
                {"item_id": items["DANA KIYMA"], "incoming": "30", "used": "35", "counted": "7"}
            ]
        },
    )

    months = api.get(f"/branches/{branch}/monthly-counts").json()
    assert [m["period"] for m in months] == ["2026-09", "2026-08"]
    aug = months[1]["totals"]
    assert (aug["eksik"], Decimal(str(aug["eksik_value"]))) == (2, Decimal("-500"))
    assert Decimal(str(aug["waste_value"])) == Decimal("800")

    d = api.get(f"/branches/{branch}/dashboard?period=2026-09").json()
    assert d["current"]["status"] == "draft" and d["current"]["totals"]["counted"] == 1
    assert d["focus_period"] == "2026-09"
    assert [t["period"] for t in d["trend"]] == ["2026-08", "2026-09"]
    assert d["top_missing"] == []  # September has nothing missing yet

    d = api.get(f"/branches/{branch}/dashboard?period=2026-07").json()
    assert d["current"] is None and d["focus_period"] == "2026-09"

    # Asking for August ranks August's lines.
    d8 = api.get(f"/branches/{branch}/dashboard?period=2026-08").json()
    assert [x["name"] for x in d8["top_missing"]] == ["DANA KIYMA", "PATATES"]
    assert d8["top_waste"][0]["name"] == "DANA KIYMA"
    food = next(g for g in d8["groups"] if g["name"] == "Gıda Ürünleri")
    assert food["eksik"] == 2 and Decimal(str(food["waste_value"])) == Decimal("800")

    h = api.get(f"/branches/{branch}/count-items/{items['DANA KIYMA']}/history").json()
    assert h["item"]["name"] == "DANA KIYMA"
    assert [(m["period"], m["difference_status"]) for m in h["months"]] == [
        ("2026-08", "eksik"),
        ("2026-09", "tamam"),
    ]
    assert Decimal(str(h["months"][1]["opening"])) == Decimal("12")

    ov = api.get("/monthly-counts/overview?period=2026-08").json()
    assert [(o["status"], o["counted_by_name"]) for o in ov] == [("closed", "Ali")]
    assert api.get("/monthly-counts/overview?period=2026-13").status_code == 422
    assert api.get(f"/branches/{branch}/dashboard?period=bad").status_code == 422


def test_dashboard_does_not_create_a_month(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    assert api.get(f"/branches/{branch}/dashboard?period=2026-08").json()["current"] is None
    assert api.get(f"/branches/{branch}/monthly-counts").json() == []


def test_user_list_and_password_reset(client, session: Session) -> None:
    owner = new_tenant(session, client)
    branch = setup_branch(owner)
    uid = owner.post(
        "/users",
        {"email": "c@example.com", "password": PASSWORD, "role": "counter", "branch_ids": [branch]},
    ).json()["id"]
    users = {u["email"]: u for u in owner.get("/users").json()}
    assert users["c@example.com"]["branch_ids"] == [branch]
    assert users["c@example.com"]["role"] == "counter"

    counter = Api(client, "c@example.com")
    res = owner.post(f"/users/{uid}/password", {"new_password": "reset-by-owner-123"})
    assert res.status_code == 204
    assert counter.get("/branches").status_code == 401  # old session ended
    Api(client, "c@example.com", "reset-by-owner-123")
    me = owner.get("/me").json()
    assert owner.post(f"/users/{me['id']}/password", {"new_password": "x" * 12}).status_code == 422
    assert me["role_label"] == "İşletme sahibi"
    assert [r["value"] for r in me["roles"]] == ["owner", "branch_manager", "counter", "viewer"]


def test_audit_log_has_email_filter_and_paging(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    api.put(f"/branches/{branch}/monthly-counts/2026-08", {"lines": []})
    email = api.get("/me").json()["email"]
    events = api.get("/audit-events?action=monthly_count.").json()
    assert [e["action"] for e in events] == ["monthly_count.save", "monthly_count.open"]
    assert {e["user_email"] for e in events} == {email}
    page2 = api.get("/audit-events?limit=1&offset=1&action=monthly_count.").json()
    assert [e["action"] for e in page2] == ["monthly_count.open"]
    # '%' and '_' in the filter are literal, not wildcards
    assert api.get("/audit-events?action=%25").json() == []


def test_setup_status_lists_what_is_missing(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    st = api.get("/setup-status").json()
    assert st["items"] == 98 and len(st["items_without_price"]) == 98
    assert st["products"] == 0 and st["unconverted"] == []
    api.post("/menu-products", {"name": "Boş Burger"})
    api.post("/menu-products/load-defaults?add_missing_items=true")
    api.post("/count-items/apply-supplier-data")
    st = api.get("/setup-status").json()
    assert st["products_without_recipe"] == ["Boş Burger"]
    assert st["products_without_price"] == ["Boş Burger"]
    assert "ICEBERG" in st["recipe_items_without_price"]
    assert st["unconverted"] == []  # pickle slices: ~285 per kg from the supplier data
    d = api.get(f"/branches/{branch}/dashboard?period=2026-08").json()
    assert d["current"] is None
    api.put(f"/branches/{branch}/monthly-counts/2026-08", {"lines": []})
    d = api.get(f"/branches/{branch}/dashboard?period=2026-08").json()
    assert d["current"]["sales_entered"] is False
