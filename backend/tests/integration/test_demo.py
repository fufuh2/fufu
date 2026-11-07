"""The isolated demo business."""

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.demo import ensure_demo
from tests.conftest import Api, new_tenant, setup_branch


def test_demo_business_is_seeded_and_isolated(client, session: Session) -> None:
    real = new_tenant(session, client, "Gerçek")
    setup_branch(real)
    result = ensure_demo(session, "demo@demo.com", "123456", today=date(2026, 11, 7))
    assert result.startswith("created")
    assert ensure_demo(session, "demo@demo.com", "123456").startswith("skipped")

    demo = Api(client, "demo@demo.com", "123456")
    branches = demo.get("/branches").json()
    assert [b["name"] for b in branches] == ["Demo Şube"]  # never the real business
    b = branches[0]["id"]
    months = demo.get(f"/branches/{b}/monthly-counts").json()
    assert [(m["period"], m["status"]) for m in months] == [
        ("2026-11", "draft"),
        ("2026-10", "closed"),
    ]
    pl = demo.get(f"/branches/{b}/profit-loss?period=2026-10").json()
    assert float(pl["revenue"]) > 0 and pl["material_basis"].startswith("gerçek")
    assert float(pl["labor_cost"]) > 42000 and float(pl["expense_total"]) == 69700
    assert len(demo.get(f"/branches/{b}/receipts?period=2026-10").json()["receipts"]) == 3
    # the demo owner cannot reach the real business, and the real owner sees no demo data
    real_branch = real.get("/branches").json()[0]["id"]
    assert demo.get(f"/branches/{real_branch}/monthly-counts").status_code == 404
    assert [x["name"] for x in real.get("/branches").json()] == ["Kadikoy"]
    # the short password is allowed only for this account; normal users keep the 12-char rule
    res = demo.post(
        "/users", {"email": "x@demo.com", "password": "123456", "role": "viewer", "branch_ids": [b]}
    )
    assert res.status_code == 422
    assert session.execute(text("SELECT count(*) FROM tenants")).scalar() == 2
