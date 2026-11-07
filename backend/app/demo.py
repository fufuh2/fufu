"""An isolated demo business with sample data, for showing the system to others.

Created on start when OPSX_DEMO_EMAIL and OPSX_DEMO_PASSWORD are set and that e-mail does not
exist yet. It is a separate business (tenant): its owner cannot see or change any other
business. The password may be shorter than the normal 12-character rule because only demo
data sits behind it.

OPSX_DEMO_RESET=<any word> starts the demo over once: the old demo owner is deactivated and
renamed (the audit log is append-only, so the old business cannot be deleted) and a fresh demo
is created. A word that was already used does nothing, so restarts do not create new demos.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services import admin, erp, menu, monthly

DEMO_NAME = "Demo Burger (örnek veri)"


def _month(d: date, delta: int) -> date:
    m = d.month - 1 + delta
    return date(d.year + m // 12, m % 12 + 1, 1)


def ensure_demo(
    session: Session, email: str, password: str, today: date | None = None, reset: str = ""
) -> str:
    email = email.strip().lower()
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        retired = f"retired-{reset.strip().lower()}.{email}"
        if not reset.strip() or session.scalar(select(User.id).where(User.email == retired)):
            return "skipped: demo account exists"
        existing.email, existing.is_active = retired, False
        existing.token_version += 1
        session.flush()
    today = today or date.today()
    _, owner = admin.bootstrap_tenant(
        session,
        name=DEMO_NAME,
        owner_email=email,
        owner_password=password,
        allow_weak_password=True,
    )
    actor = admin.load_actor(session, owner.id)
    assert actor is not None  # noqa: S101 - just created
    branch = admin.create_branch(session, actor, "Demo Şube").id
    monthly.load_default_items(session, actor)
    menu.load_default_menu(session, actor, add_missing=True)
    menu.apply_supplier_data(session, actor, overwrite=False)
    rnd = random.Random(42)  # noqa: S311 - sample data, not security
    items = {i.name: i for i in monthly.list_items(session, actor)}
    products = [p for p in menu.list_products(session, actor) if p["price"]]
    sup = erp.save_supplier(session, actor, None, {"name": "Taff Merkez Depo"}).id
    erp.save_supplier(session, actor, None, {"name": "Manav Ahmet"})

    last, this = _month(today, -1), today.replace(day=1)
    # Last month: deliveries, every day's sales, expenses, staff; then counted and closed.
    for start, days in ((last, (this - last).days), (this, max(today.day - 1, 1))):
        for d in (1, 10, 20):
            if d > days:
                continue
            erp.create_receipt(
                session,
                actor,
                branch,
                day=start + timedelta(days=d - 1),
                supplier_id=sup,
                document_no=f"IRS-{start:%m}{d:02d}",
                note=None,
                lines=[
                    (items["DANA KIYMA"].id, Decimal("60"), Decimal("970")),
                    (items["HAMBURGER EKMEĞİ"].id, Decimal("400"), Decimal("16.50")),
                    (items["CHEDAR PEYNİRİ"].id, Decimal("9"), Decimal("505")),
                    (items["YOUKİ SOS"].id, Decimal("6"), Decimal("258.10")),
                    (items["PATATES"].id, Decimal("80"), Decimal("25")),
                ],
                update_prices=False,
            )
        for d in range(days):
            erp.save_day(
                session,
                actor,
                branch,
                start + timedelta(days=d),
                [(p["id"], Decimal(rnd.randint(0, 12)), None) for p in products],
            )
        for cat, amount in (
            ("kira", 45000),
            ("elektrik", 8200),
            ("dogalgaz", 3100),
            ("internet_telefon", 900),
            ("komisyon", 12500),
        ):
            erp.add_expense(
                session, actor, branch, day=start, category=cat, amount=Decimal(amount), note=None
            )
    usta = erp.save_employee(
        session,
        actor,
        branch,
        None,
        {"name": "Ali Usta", "title": "Usta", "pay_type": "monthly", "wage": Decimal("42000")},
    )
    kasa = erp.save_employee(
        session,
        actor,
        branch,
        None,
        {"name": "Ayşe", "title": "Kasiyer", "pay_type": "hourly", "wage": Decimal("160")},
    )
    for start, days in ((last, (this - last).days), (this, max(today.day - 1, 1))):
        for d in range(0, days, 2):
            erp.add_shift(
                session,
                actor,
                kasa.id,
                day=start + timedelta(days=d),
                start="16:00",
                end="23:30",
                hours=None,
                note=None,
            )
    _ = usta

    # Count last month: counted = expected with a small, realistic shortfall on some items.
    sheet = monthly.sheet(
        session, actor, monthly.open_month(session, actor, branch, last.strftime("%Y-%m"))
    )
    lines = []
    for g in sheet["groups"]:
        for ln in g["lines"]:
            expected = Decimal(str(ln["expected"]))
            opening = None
            if expected < 0:  # used more than came in: give the month an opening stock
                opening = (-expected * Decimal("1.2")).quantize(Decimal("0.001"))
                expected += opening
            counted = expected
            if counted > 0 and rnd.random() < 0.3:
                counted = (counted * Decimal(str(rnd.uniform(0.9, 0.99)))).quantize(
                    Decimal("0.001")
                )
            lines.append(
                monthly.LineUpdate(item_id=ln["item_id"], opening=opening, counted=counted)
            )
    monthly.save_lines(
        session, actor, branch, last.strftime("%Y-%m"), lines, {"counted_by_name": "Demo Sayım"}
    )
    monthly.close_month(session, actor, branch, last.strftime("%Y-%m"))
    return f"created demo business for {email}"
