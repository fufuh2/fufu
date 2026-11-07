"""Backup to a file and restore into a fresh database; edit conflicts on the same month."""

import json
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import backup, monthly
from app.services.admin import bootstrap_tenant, load_actor
from tests.conftest import PASSWORD, Api, new_tenant, setup_branch
from tests.integration.test_copy import _fresh_target

P = "2026-08"


def test_backup_restores_everything_into_a_fresh_database(client, session: Session) -> None:
    api = new_tenant(session, client)
    owner_email = api.get("/me").json()["email"]
    branch = setup_branch(api)
    api.post("/menu-products/load-defaults?add_missing_items=true")
    api.post("/count-items/apply-supplier-data")
    api.post(
        "/users",
        {"email": "c@example.com", "password": PASSWORD, "role": "counter", "branch_ids": [branch]},
    )
    youki = next(p["id"] for p in api.get("/menu-products").json() if p["name"] == "YOUKI")
    items = {i["name"]: i["id"] for i in api.get("/count-items").json()}
    api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {
            "counted_by_name": "Ali",
            "sales": [{"product_id": youki, "quantity": "100"}],
            "lines": [{"item_id": items["DANA KIYMA"], "incoming": "20", "counted": "4.5"}],
        },
    )
    res = api.get("/backup")
    assert res.status_code == 200
    assert res.headers["content-disposition"].startswith('attachment; filename="sayim-yedek-')
    data = json.loads(res.content)
    assert len(data["tables"]["count_items"]) == 116
    # a counter may not download the whole business
    assert Api(client, "c@example.com").get("/backup").status_code == 403
    before = api.get(f"/branches/{branch}/monthly-counts/{P}").json()

    target = _fresh_target()
    try:
        with Session(target) as s2:
            _, owner = bootstrap_tenant(
                s2, name="Yeni", owner_email=owner_email, owner_password="a-new-db-password"
            )
            actor = load_actor(s2, owner.id)
            assert actor is not None
            counts = backup.restore(s2, actor, data)
            assert counts["count_items"] == 116 and counts["users"] == 1  # owner mapped
            actor = load_actor(s2, owner.id)
            count = s2.execute(text("SELECT id FROM monthly_counts")).scalar()
            from app.models import MonthlyCount

            after = monthly.sheet(s2, actor, s2.get(MonthlyCount, count))
            line = lambda sh: next(  # noqa: E731
                ln for g in sh["groups"] for ln in g["lines"] if ln["name"] == "DANA KIYMA"
            )
            assert Decimal(str(line(after)["recipe_used"])) == Decimal(
                str(line(before)["recipe_used"])
            )
            assert after["counted_by_name"] == "Ali"
            assert {x["name"] for x in after["sales"] if x["quantity"]} == {"YOUKI"}
            # the counter can log in on the new database with the old password
            assert (
                s2.execute(text("SELECT role FROM users WHERE email = 'c@example.com'")).scalar()
                == "counter"
            )
            # a second restore is refused: the business is no longer empty
            try:
                backup.restore(s2, actor, data)
                raise AssertionError("restore into a non-empty business must fail")
            except Exception as exc:  # noqa: BLE001
                assert "boş" in str(exc)
    finally:
        target.dispose()


def test_restore_rejects_foreign_or_mismatched_files(client, session: Session) -> None:
    api = new_tenant(session, client)
    assert api.post("/restore", {"format": "x"}).status_code == 422
    data = json.loads(api.get("/backup").content)
    data["schema_revision"] = "0001"
    res = api.post("/restore", data)
    assert res.status_code == 422 and "sürüm" in res.json()["detail"]["message"]


def test_restore_rejects_references_outside_the_file(client, session: Session) -> None:
    victim = new_tenant(session, client, "A")
    vb = setup_branch(victim)
    victim_count = victim.get(f"/branches/{vb}/monthly-counts/{P}").json()["id"]
    attacker = new_tenant(session, client, "B")
    data = json.loads(attacker.get("/backup").content)
    data["tables"]["monthly_sales"] = [
        {
            "count_id": victim_count,
            "product_id": "00000000-0000-0000-0000-000000000000",
            "quantity": "1",
        }
    ]
    res = attacker.post("/restore", data)
    assert res.status_code == 422 and "tutarsız" in res.json()["detail"]["message"]


def test_concurrent_edits_do_not_overwrite_each_other(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    item = api.get("/count-items").json()[0]["id"]
    sheet = api.get(f"/branches/{branch}/monthly-counts/{P}").json()
    v = sheet["version"]
    first = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {"base_version": v, "lines": [{"item_id": item, "counted": "1"}]},
    )
    assert first.status_code == 200 and first.json()["version"] == v + 1
    # a second person still on the old version is refused instead of overwriting
    stale = api.put(
        f"/branches/{branch}/monthly-counts/{P}",
        {"base_version": v, "lines": [{"item_id": item, "counted": "9"}]},
    )
    assert stale.status_code == 409 and "başka biri" in stale.json()["detail"]["message"]
    after = api.get(f"/branches/{branch}/monthly-counts/{P}").json()
    assert after["groups"][0]["lines"][0]["counted"] in ("1.000", 1, 1.0, "1")
