"""Business modules around the monthly count: purchasing, daily sales, expenses, staff and
the monthly profit and loss.

They feed the count sheet instead of duplicating it:
- a goods receipt adds its quantities to the month's 'Gelen' (incoming) of each item;
- daily sales are summed into the month's menu sales, which drive 'Satıştan (reçete)'.
Both need the month to be open (draft); a closed month stays as it was closed.
"""

import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.monthly import Q2
from app.domain.permissions import Perm, has_perm
from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import (
    EXPENSE_CATEGORIES,
    CountItem,
    DailySale,
    Employee,
    Expense,
    MenuProduct,
    MonthlyCount,
    MonthlyCountLine,
    MonthlySale,
    PurchaseReceipt,
    PurchaseReceiptLine,
    Shift,
    Supplier,
)
from app.security import Actor
from app.services import audit, monthly
from app.services.scope import get_active_branch, get_branch

ZERO = Decimal("0")


def _money(v: Decimal) -> Decimal:
    return v.quantize(Q2, rounding=ROUND_HALF_EVEN)


def _period_bounds(period: str) -> tuple[date, date]:
    start = monthly._period_date(period)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def _require_any(actor: Actor, *perms: Perm) -> None:
    if not any(has_perm(actor.role, p) for p in perms):
        raise Forbidden("bu işlem için yetkiniz yok")


def _draft_month(session: Session, actor: Actor, branch_id: uuid.UUID, day: date) -> MonthlyCount:
    """The (possibly new) month of `day`, locked for update; refused when it is closed."""
    period = day.strftime("%Y-%m")
    monthly.open_month(session, actor, branch_id, period)
    count = monthly._get_count(session, actor, branch_id, day.replace(day=1), lock=True)
    if count is None:  # pragma: no cover - open_month just created it
        raise NotFound("ay bulunamadı")
    if count.status != "draft":
        raise Conflict(f"{period} sayımı kapatılmış; bu tarihe kayıt için önce ay yeniden açılmalı")
    return count


def _line(session: Session, count: MonthlyCount, item_id: uuid.UUID) -> MonthlyCountLine:
    line = session.scalar(
        select(MonthlyCountLine)
        .where(MonthlyCountLine.count_id == count.id, MonthlyCountLine.item_id == item_id)
        .with_for_update()
    )
    if line is None:
        raise Invalid("ürün bu ayın sayımında yok (pasif olabilir)")
    return line


# --- suppliers -------------------------------------------------------------------------------


def list_suppliers(session: Session, actor: Actor) -> list[Supplier]:
    _require_any(actor, Perm.PURCHASE_MANAGE, Perm.FINANCE_READ)
    return list(
        session.scalars(
            select(Supplier).where(Supplier.tenant_id == actor.tenant_id).order_by(Supplier.name)
        )
    )


def save_supplier(
    session: Session, actor: Actor, supplier_id: uuid.UUID | None, fields: dict[str, Any]
) -> Supplier:
    actor.require(Perm.PURCHASE_MANAGE)
    if supplier_id is None:
        s = Supplier(tenant_id=actor.tenant_id, name=fields.get("name", "").strip())
        session.add(s)
    else:
        found = session.scalar(
            select(Supplier).where(
                Supplier.id == supplier_id, Supplier.tenant_id == actor.tenant_id
            )
        )
        if found is None:
            raise NotFound("tedarikçi bulunamadı")
        s = found
    for key in ("name", "phone", "tax_no", "note", "is_active"):
        if key in fields:
            if key in ("name", "is_active") and fields[key] is None:
                raise Invalid(f"{key} boş bırakılamaz")
            v = fields[key]
            setattr(s, key, v.strip() if isinstance(v, str) else v)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("bu isimde bir tedarikçi zaten var") from exc
    audit.record(session, actor, "supplier.save", "supplier", s.id, {"name": s.name})
    session.commit()
    return s


# --- goods receipts --------------------------------------------------------------------------


def create_receipt(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    *,
    day: date,
    supplier_id: uuid.UUID | None,
    document_no: str | None,
    note: str | None,
    lines: list[tuple[uuid.UUID, Decimal, Decimal | None]],
    update_prices: bool,
) -> PurchaseReceipt:
    actor.require(Perm.PURCHASE_MANAGE)
    get_active_branch(session, actor, branch_id)
    if not lines:
        raise Invalid("en az bir ürün satırı girin")
    if supplier_id is not None and (
        session.scalar(
            select(Supplier.id).where(
                Supplier.id == supplier_id, Supplier.tenant_id == actor.tenant_id
            )
        )
        is None
    ):
        raise NotFound("tedarikçi bulunamadı")
    count = _draft_month(session, actor, branch_id, day)
    receipt = PurchaseReceipt(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        supplier_id=supplier_id,
        date=day,
        document_no=(document_no or "").strip() or None,
        note=(note or "").strip() or None,
        created_by=actor.user_id,
    )
    session.add(receipt)
    session.flush()
    seen: set[uuid.UUID] = set()
    for item_id, qty, price in lines:
        if item_id in seen:
            raise Invalid("bir ürün aynı belgede bir kez yer alabilir")
        seen.add(item_id)
        item = session.scalar(
            select(CountItem).where(CountItem.id == item_id, CountItem.tenant_id == actor.tenant_id)
        )
        if item is None:
            raise NotFound("ürün bulunamadı")
        line = _line(session, count, item.id)
        line.incoming += qty
        session.add(
            PurchaseReceiptLine(
                receipt_id=receipt.id, item_id=item.id, quantity=qty, unit_price=price
            )
        )
        if update_prices and price is not None:
            item.unit_price = price
    count.version += 1
    audit.record(
        session,
        actor,
        "purchase.receipt",
        "purchase_receipt",
        receipt.id,
        {"branch_id": str(branch_id), "date": day.isoformat(), "lines": len(lines)},
    )
    session.commit()
    return receipt


def delete_receipt(session: Session, actor: Actor, receipt_id: uuid.UUID) -> None:
    actor.require(Perm.PURCHASE_MANAGE)
    receipt = session.scalar(
        select(PurchaseReceipt).where(
            PurchaseReceipt.id == receipt_id, PurchaseReceipt.tenant_id == actor.tenant_id
        )
    )
    if receipt is None:
        raise NotFound("belge bulunamadı")
    get_active_branch(session, actor, receipt.branch_id)
    count = _draft_month(session, actor, receipt.branch_id, receipt.date)
    for rl in session.scalars(
        select(PurchaseReceiptLine).where(PurchaseReceiptLine.receipt_id == receipt.id)
    ):
        line = _line(session, count, rl.item_id)
        if line.incoming < rl.quantity:
            raise Conflict(
                "bu belgenin miktarı sayımdaki 'Gelen'den büyük (elle azaltılmış); önce sayımda "
                "Gelen'i düzeltin"
            )
        line.incoming -= rl.quantity
    session.execute(delete(PurchaseReceiptLine).where(PurchaseReceiptLine.receipt_id == receipt.id))
    session.delete(receipt)
    count.version += 1
    audit.record(
        session,
        actor,
        "purchase.receipt_delete",
        "purchase_receipt",
        receipt_id,
        {"branch_id": str(receipt.branch_id), "date": receipt.date.isoformat()},
    )
    session.commit()


def list_receipts(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: str
) -> dict[str, Any]:
    _require_any(actor, Perm.PURCHASE_MANAGE, Perm.FINANCE_READ)
    get_branch(session, actor, branch_id)
    start, end = _period_bounds(period)
    receipts = list(
        session.scalars(
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.branch_id == branch_id,
                PurchaseReceipt.date >= start,
                PurchaseReceipt.date < end,
            )
            .order_by(PurchaseReceipt.date.desc(), PurchaseReceipt.created_at.desc())
        )
    )
    suppliers = {s.id: s.name for s in list_suppliers(session, actor)}
    names = {
        i.id: (i.name, i.unit)
        for i in session.scalars(select(CountItem).where(CountItem.tenant_id == actor.tenant_id))
    }
    out: list[dict[str, Any]] = []
    by_supplier: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in receipts:
        lines = []
        total = ZERO
        for rl in session.scalars(
            select(PurchaseReceiptLine).where(PurchaseReceiptLine.receipt_id == r.id)
        ):
            value = None if rl.unit_price is None else _money(rl.quantity * rl.unit_price)
            total += value or ZERO
            lines.append(
                {
                    "item_id": rl.item_id,
                    "item_name": names[rl.item_id][0],
                    "unit": names[rl.item_id][1],
                    "quantity": rl.quantity,
                    "unit_price": rl.unit_price,
                    "total": value,
                }
            )
        supplier = suppliers.get(r.supplier_id) if r.supplier_id else None
        by_supplier[supplier or "—"] += total
        out.append(
            {
                "id": r.id,
                "date": r.date,
                "supplier": supplier,
                "document_no": r.document_no,
                "note": r.note,
                "total": total,
                "lines": lines,
            }
        )
    return {
        "period": period,
        "receipts": out,
        "total": sum((r["total"] for r in out), ZERO),
        "by_supplier": [{"supplier": k, "total": v} for k, v in sorted(by_supplier.items())],
    }


# --- daily sales -----------------------------------------------------------------------------


def _resync_month_sales(session: Session, count: MonthlyCount) -> None:
    start, end = count.period, _period_bounds(count.period.strftime("%Y-%m"))[1]
    totals = dict(
        session.execute(
            select(DailySale.product_id, func.sum(DailySale.quantity))
            .where(
                DailySale.branch_id == count.branch_id,
                DailySale.date >= start,
                DailySale.date < end,
            )
            .group_by(DailySale.product_id)
        ).all()
    )
    session.execute(delete(MonthlySale).where(MonthlySale.count_id == count.id))
    for pid, qty in totals.items():
        if qty:
            session.add(MonthlySale(count_id=count.id, product_id=pid, quantity=qty))
    session.flush()
    monthly._sync_draft(session, count)


def get_day(session: Session, actor: Actor, branch_id: uuid.UUID, day: date) -> dict[str, Any]:
    _require_any(actor, Perm.SALES_MANAGE, Perm.FINANCE_READ)
    get_branch(session, actor, branch_id)
    rows = {
        s.product_id: s
        for s in session.scalars(
            select(DailySale).where(DailySale.branch_id == branch_id, DailySale.date == day)
        )
    }
    products = []
    for p in session.scalars(
        select(MenuProduct)
        .where(MenuProduct.tenant_id == actor.tenant_id)
        .order_by(MenuProduct.sort_order, MenuProduct.name)
    ):
        row = rows.get(p.id)
        if not p.is_active and row is None:
            continue
        products.append(
            {
                "product_id": p.id,
                "name": p.name,
                "menu_price": p.price,
                "quantity": row.quantity if row else ZERO,
                "unit_price": row.unit_price if row else p.price,
            }
        )
    return {"date": day, "products": products}


def save_day(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    day: date,
    entries: list[tuple[uuid.UUID, Decimal, Decimal | None]],
) -> dict[str, Any]:
    actor.require(Perm.SALES_MANAGE)
    get_active_branch(session, actor, branch_id)
    count = _draft_month(session, actor, branch_id, day)
    for pid, qty, price in entries:
        product = session.scalar(
            select(MenuProduct).where(
                MenuProduct.id == pid, MenuProduct.tenant_id == actor.tenant_id
            )
        )
        if product is None:
            raise NotFound("menü ürünü bulunamadı")
        row = session.scalar(
            select(DailySale).where(
                DailySale.branch_id == branch_id, DailySale.date == day, DailySale.product_id == pid
            )
        )
        if qty == 0:
            if row is not None:
                session.delete(row)
            continue
        if row is None:
            row = DailySale(
                tenant_id=actor.tenant_id, branch_id=branch_id, date=day, product_id=pid
            )
            session.add(row)
        row.quantity = qty
        row.unit_price = price if price is not None else product.price
    session.flush()
    _resync_month_sales(session, count)
    count.version += 1
    audit.record(
        session,
        actor,
        "sales.day",
        "daily_sales",
        None,
        {"branch_id": str(branch_id), "date": day.isoformat(), "lines": len(entries)},
    )
    session.commit()
    return get_day(session, actor, branch_id, day)


def sales_report(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: str
) -> dict[str, Any]:
    _require_any(actor, Perm.SALES_MANAGE, Perm.FINANCE_READ)
    get_branch(session, actor, branch_id)
    start, end = _period_bounds(period)
    names = {
        p.id: p.name
        for p in session.scalars(
            select(MenuProduct).where(MenuProduct.tenant_id == actor.tenant_id)
        )
    }
    by_day: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"quantity": ZERO, "revenue": ZERO}
    )
    by_product: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(
        lambda: {"quantity": ZERO, "revenue": ZERO}
    )
    for s in session.scalars(
        select(DailySale).where(
            DailySale.branch_id == branch_id, DailySale.date >= start, DailySale.date < end
        )
    ):
        rev = _money(s.quantity * (s.unit_price or ZERO))
        by_day[s.date]["quantity"] += s.quantity
        by_day[s.date]["revenue"] += rev
        by_product[s.product_id]["quantity"] += s.quantity
        by_product[s.product_id]["revenue"] += rev
    return {
        "period": period,
        "days": [{"date": d, **v} for d, v in sorted(by_day.items())],
        "products": sorted(
            ({"product_id": k, "name": names.get(k, "?"), **v} for k, v in by_product.items()),
            key=lambda x: -x["revenue"],
        ),
        "quantity": sum((v["quantity"] for v in by_day.values()), ZERO),
        "revenue": sum((v["revenue"] for v in by_day.values()), ZERO),
    }


# --- expenses --------------------------------------------------------------------------------

EXPENSE_LABELS = {
    "kira": "Kira",
    "elektrik": "Elektrik",
    "su": "Su",
    "dogalgaz": "Doğalgaz",
    "internet_telefon": "İnternet / telefon",
    "personel_diger": "Personel (maaş dışı: yemek, yol, prim)",
    "bakim_onarim": "Bakım / onarım",
    "reklam": "Reklam",
    "komisyon": "Komisyon (yemek siteleri, POS)",
    "vergi_harc": "Vergi / harç",
    "diger": "Diğer",
}


def add_expense(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    *,
    day: date,
    category: str,
    amount: Decimal,
    note: str | None,
) -> Expense:
    actor.require(Perm.EXPENSE_MANAGE)
    get_active_branch(session, actor, branch_id)
    if category not in EXPENSE_CATEGORIES:
        raise Invalid("geçersiz gider türü")
    e = Expense(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        date=day,
        category=category,
        amount=amount,
        note=(note or "").strip() or None,
        created_by=actor.user_id,
    )
    session.add(e)
    session.flush()
    audit.record(
        session,
        actor,
        "expense.add",
        "expense",
        e.id,
        {"branch_id": str(branch_id), "category": category, "amount": str(amount)},
    )
    session.commit()
    return e


def delete_expense(session: Session, actor: Actor, expense_id: uuid.UUID) -> None:
    actor.require(Perm.EXPENSE_MANAGE)
    e = session.scalar(
        select(Expense).where(Expense.id == expense_id, Expense.tenant_id == actor.tenant_id)
    )
    if e is None:
        raise NotFound("gider bulunamadı")
    get_branch(session, actor, e.branch_id)
    session.delete(e)
    audit.record(
        session,
        actor,
        "expense.delete",
        "expense",
        expense_id,
        {"branch_id": str(e.branch_id), "amount": str(e.amount)},
    )
    session.commit()


def list_expenses(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: str
) -> dict[str, Any]:
    _require_any(actor, Perm.EXPENSE_MANAGE, Perm.FINANCE_READ)
    get_branch(session, actor, branch_id)
    start, end = _period_bounds(period)
    rows = list(
        session.scalars(
            select(Expense)
            .where(Expense.branch_id == branch_id, Expense.date >= start, Expense.date < end)
            .order_by(Expense.date.desc(), Expense.created_at.desc())
        )
    )
    by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in rows:
        by_cat[e.category] += e.amount
    return {
        "period": period,
        "expenses": [
            {
                "id": e.id,
                "date": e.date,
                "category": e.category,
                "category_label": EXPENSE_LABELS[e.category],
                "amount": e.amount,
                "note": e.note,
            }
            for e in rows
        ],
        "by_category": [
            {"category": k, "label": EXPENSE_LABELS[k], "total": v}
            for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])
        ],
        "total": sum(by_cat.values(), ZERO),
        "categories": [{"value": k, "label": v} for k, v in EXPENSE_LABELS.items()],
    }


# --- staff -----------------------------------------------------------------------------------


def list_employees(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[Employee]:
    _require_any(actor, Perm.STAFF_MANAGE, Perm.FINANCE_READ)
    get_branch(session, actor, branch_id)
    return list(
        session.scalars(
            select(Employee)
            .where(Employee.branch_id == branch_id)
            .order_by(Employee.is_active.desc(), Employee.name)
        )
    )


def save_employee(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID | None,
    employee_id: uuid.UUID | None,
    fields: dict[str, Any],
) -> Employee:
    actor.require(Perm.STAFF_MANAGE)
    if employee_id is None:
        if branch_id is None:
            raise Invalid("şube gerekli")
        get_active_branch(session, actor, branch_id)
        emp = Employee(tenant_id=actor.tenant_id, branch_id=branch_id)
        session.add(emp)
    else:
        found = session.scalar(
            select(Employee).where(
                Employee.id == employee_id, Employee.tenant_id == actor.tenant_id
            )
        )
        if found is None:
            raise NotFound("personel bulunamadı")
        get_branch(session, actor, found.branch_id)
        emp = found
    for key in ("name", "title", "phone", "pay_type", "wage", "start_date", "is_active"):
        if key in fields:
            if key in ("name", "pay_type", "wage", "is_active") and fields[key] is None:
                raise Invalid(f"{key} boş bırakılamaz")
            v = fields[key]
            setattr(emp, key, v.strip() if isinstance(v, str) else v)
    if not emp.name or emp.pay_type not in ("hourly", "monthly") or emp.wage is None:
        raise Invalid("ad, ücret türü ve ücret girilmeli")
    session.flush()
    audit.record(
        session,
        actor,
        "employee.save",
        "employee",
        emp.id,
        {"branch_id": str(emp.branch_id), "name": emp.name},
    )
    session.commit()
    return emp


def _hours(start: str | None, end: str | None) -> Decimal | None:
    if not start or not end:
        return None
    try:
        a = datetime.strptime(start, "%H:%M")
        b = datetime.strptime(end, "%H:%M")
    except ValueError as exc:
        raise Invalid("saat SS:DD biçiminde olmalı (örn. 09:30)") from exc
    minutes = (b - a).seconds // 60  # an end before the start means past midnight
    return (Decimal(minutes) / 60).quantize(Q2, rounding=ROUND_HALF_EVEN)


def add_shift(
    session: Session,
    actor: Actor,
    employee_id: uuid.UUID,
    *,
    day: date,
    start: str | None,
    end: str | None,
    hours: Decimal | None,
    note: str | None,
) -> Shift:
    actor.require(Perm.STAFF_MANAGE)
    emp = session.scalar(
        select(Employee).where(Employee.id == employee_id, Employee.tenant_id == actor.tenant_id)
    )
    if emp is None:
        raise NotFound("personel bulunamadı")
    get_active_branch(session, actor, emp.branch_id)
    worked = hours if hours is not None else _hours(start, end)
    if worked is None:
        raise Invalid("başlangıç-bitiş saati ya da çalışılan saat girilmeli")
    if worked > 24:
        raise Invalid("bir vardiya 24 saati geçemez")
    sh = Shift(
        tenant_id=actor.tenant_id,
        employee_id=emp.id,
        date=day,
        start=start or None,
        end=end or None,
        hours=worked,
        note=(note or "").strip() or None,
    )
    session.add(sh)
    session.flush()
    audit.record(
        session,
        actor,
        "shift.add",
        "shift",
        sh.id,
        {"branch_id": str(emp.branch_id), "employee": emp.name, "date": day.isoformat()},
    )
    session.commit()
    return sh


def delete_shift(session: Session, actor: Actor, shift_id: int) -> None:
    actor.require(Perm.STAFF_MANAGE)
    sh = session.scalar(
        select(Shift).where(Shift.id == shift_id, Shift.tenant_id == actor.tenant_id)
    )
    if sh is None:
        raise NotFound("vardiya bulunamadı")
    emp = session.get(Employee, sh.employee_id)
    if emp is not None:
        get_branch(session, actor, emp.branch_id)
    session.delete(sh)
    audit.record(session, actor, "shift.delete", "shift", shift_id, {})
    session.commit()


def staff_month(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: str
) -> dict[str, Any]:
    """Shifts of the month and labour cost: hourly staff hours × wage, monthly staff their
    monthly cost when active."""
    employees = list_employees(session, actor, branch_id)
    start, end = _period_bounds(period)
    shifts = list(
        session.scalars(
            select(Shift)
            .where(
                Shift.employee_id.in_([e.id for e in employees]),
                Shift.date >= start,
                Shift.date < end,
            )
            .order_by(Shift.date, Shift.start)
        )
    )
    hours: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for sh in shifts:
        hours[sh.employee_id] += sh.hours
    people: list[dict[str, Any]] = []
    for e in employees:
        if e.pay_type == "hourly":
            cost = _money(hours[e.id] * e.wage)
        else:
            cost = e.wage if e.is_active else ZERO
        if not e.is_active and not hours[e.id]:
            continue
        people.append(
            {
                "id": e.id,
                "name": e.name,
                "title": e.title,
                "phone": e.phone,
                "pay_type": e.pay_type,
                "wage": e.wage,
                "start_date": e.start_date,
                "is_active": e.is_active,
                "hours": hours[e.id],
                "cost": cost,
            }
        )
    names = {e.id: e.name for e in employees}
    return {
        "period": period,
        "employees": people,
        "shifts": [
            {
                "id": sh.id,
                "employee_id": sh.employee_id,
                "employee": names.get(sh.employee_id),
                "date": sh.date,
                "start": sh.start,
                "end": sh.end,
                "hours": sh.hours,
                "note": sh.note,
            }
            for sh in shifts
        ],
        "hours": sum(hours.values(), ZERO),
        "labor_cost": sum((Decimal(p["cost"]) for p in people), ZERO),
    }


# --- profit and loss -------------------------------------------------------------------------


def profit_loss(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: str
) -> dict[str, Any]:
    actor.require(Perm.FINANCE_READ)
    get_branch(session, actor, branch_id)
    count = monthly._get_count(session, actor, branch_id, monthly._period_date(period))
    sales = sales_report(session, actor, branch_id, period)
    notes: list[str] = []
    revenue = sales["revenue"]
    revenue_basis = "günlük satışlar"
    sheet = None
    if count is not None:
        if count.status == "draft":
            monthly._sync_draft(session, count)
            session.commit()
        sheet = monthly.sheet(session, actor, count)
        if not sales["days"]:
            # No daily sales: the month's menu sales × menu prices.
            prices = {
                p.id: p.price
                for p in session.scalars(
                    select(MenuProduct).where(MenuProduct.tenant_id == actor.tenant_id)
                )
            }
            revenue = sum(
                (
                    _money(Decimal(str(s["quantity"])) * (prices.get(s["product_id"]) or ZERO))
                    for s in sheet["sales"]
                ),
                ZERO,
            )
            revenue_basis = "aylık menü satışları × menü fiyatı"
    purchases = (
        list_receipts(session, actor, branch_id, period)["total"]
        if has_perm(actor.role, Perm.FINANCE_READ)
        else ZERO
    )
    theoretical = actual = waste = missing = ZERO
    counted_all = False
    if sheet is not None:
        lines = [ln for g in sheet["groups"] for ln in g["lines"]]
        counted_all = all(ln["counted"] is not None for ln in lines) and bool(lines)
        for ln in lines:
            price = ln["unit_price"]
            if price is None:
                continue
            p = Decimal(str(price))
            theoretical += _money(Decimal(str(ln["recipe_used"])) * p)
            if ln["counted"] is not None:
                used = (
                    Decimal(str(ln["opening"]))
                    + Decimal(str(ln["incoming"]))
                    - Decimal(str(ln["counted"]))
                )
                actual += _money(used * p)
        waste = Decimal(str(sheet["totals"]["waste_value"]))
        missing = Decimal(str(sheet["totals"]["eksik_value"]))
        if sheet["totals"]["priced_items"] < sheet["totals"]["items"]:
            notes.append(sheet["value_coverage"] + "; fiyatsız ürünler maliyete girmez")
    else:
        notes.append("bu ay için sayım yok; malzeme maliyeti hesaplanamadı")
    material = actual if counted_all else theoretical
    material_basis = (
        "gerçek tüketim (devir + gelen − sayılan)" if counted_all else "reçeteye göre (teorik)"
    )
    if not counted_all and sheet is not None:
        notes.append("ay sayılmadığı için malzeme maliyeti reçeteden hesaplandı")
    expenses = (
        list_expenses(session, actor, branch_id, period)
        if has_perm(actor.role, Perm.FINANCE_READ)
        else {"by_category": [], "total": ZERO}
    )
    staff = staff_month(session, actor, branch_id, period)
    gross = revenue - material
    net = gross - expenses["total"] - staff["labor_cost"]

    def pct(v: Decimal) -> Decimal | None:
        return None if not revenue else (v * 100 / revenue).quantize(Decimal("0.1"))

    return {
        "period": period,
        "revenue": revenue,
        "revenue_basis": revenue_basis,
        "purchases": purchases,
        "material_cost": material,
        "material_basis": material_basis,
        "theoretical_cost": theoretical,
        "actual_cost": actual if counted_all else None,
        "waste_value": waste,
        "missing_value": missing,
        "gross_profit": gross,
        "expenses": expenses["by_category"],
        "expense_total": expenses["total"],
        "labor_cost": staff["labor_cost"],
        "labor_hours": staff["hours"],
        "net_profit": net,
        "food_cost_pct": pct(material),
        "labor_pct": pct(staff["labor_cost"]),
        "net_pct": pct(net),
        "notes": notes,
    }
