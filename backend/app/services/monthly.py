"""Monthly inventory count (aylık sayım).

One sheet per branch and month. Each line: opening (devir) + incoming (gelen) - used
(satılan/kullanılan) = expected; counted (sayılan) - expected = difference (fazla/eksik).
The opening of a month is the counted quantity of the latest *closed* earlier month and is
locked; without a closed earlier month it is entered by hand. A closed month is read-only
and keeps the unit prices it was closed with.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.monthly import compute_line, parse_period
from app.domain.permissions import Perm
from app.errors import Conflict, Invalid, NotFound
from app.models import COUNT_UNITS, CountItem, MonthlyCount, MonthlyCountLine
from app.security import Actor
from app.services import audit
from app.services.inventory import now
from app.services.scope import get_active_branch, get_branch

DEFAULT_ITEMS = Path(__file__).resolve().parent.parent / "data" / "taffburger_items.json"
MAX_QTY = Decimal("99999999999.999")


# --- product list ---------------------------------------------------------------------------


def list_items(session: Session, actor: Actor, include_inactive: bool = False) -> list[CountItem]:
    actor.require(Perm.STOCK_READ)
    stmt = select(CountItem).where(CountItem.tenant_id == actor.tenant_id)
    if not include_inactive:
        stmt = stmt.where(CountItem.is_active)
    return list(session.scalars(stmt.order_by(CountItem.sort_order, CountItem.name)))


def _check_item_fields(unit: str | None, price: Decimal | None) -> None:
    if unit is not None and unit not in COUNT_UNITS:
        raise Invalid(f"birim şunlardan biri olmalı: {', '.join(COUNT_UNITS)}")
    if price is not None and price < 0:
        raise Invalid("birim fiyat negatif olamaz")


def create_item(
    session: Session,
    actor: Actor,
    *,
    group_name: str,
    name: str,
    unit: str,
    unit_price: Decimal | None,
) -> CountItem:
    actor.require(Perm.CATALOG_MANAGE)
    _check_item_fields(unit, unit_price)
    last = session.scalar(
        select(func.max(CountItem.sort_order)).where(CountItem.tenant_id == actor.tenant_id)
    )
    item = CountItem(
        tenant_id=actor.tenant_id,
        group_name=group_name.strip(),
        name=name.strip(),
        unit=unit,
        unit_price=unit_price,
        sort_order=(last or 0) + 10,
    )
    session.add(item)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("bu isimde bir ürün zaten var") from exc
    audit.record(session, actor, "count_item.create", "count_item", item.id, {"name": item.name})
    session.commit()
    return item


def update_item(
    session: Session, actor: Actor, item_id: uuid.UUID, fields: dict[str, Any]
) -> CountItem:
    actor.require(Perm.CATALOG_MANAGE)
    item = session.scalar(
        select(CountItem).where(CountItem.id == item_id, CountItem.tenant_id == actor.tenant_id)
    )
    if item is None:
        raise NotFound("ürün bulunamadı")
    _check_item_fields(fields.get("unit"), fields.get("unit_price"))
    before = {k: str(getattr(item, k)) for k in fields}
    for key, value in fields.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("bu isimde bir ürün zaten var") from exc
    audit.record(
        session,
        actor,
        "count_item.update",
        "count_item",
        item.id,
        {"before": before, "after": {k: str(v) for k, v in fields.items()}},
    )
    session.commit()
    return item


def load_default_items(session: Session, actor: Actor) -> dict[str, int]:
    """Adds the products from the restaurant's count form; existing names are skipped."""
    actor.require(Perm.CATALOG_MANAGE)
    data = json.loads(DEFAULT_ITEMS.read_text(encoding="utf-8"))["items"]
    existing = set(
        session.scalars(select(CountItem.name).where(CountItem.tenant_id == actor.tenant_id))
    )
    added = 0
    for pos, row in enumerate(data):
        if row["name"] in existing:
            continue
        session.add(
            CountItem(
                tenant_id=actor.tenant_id,
                group_name=row["group"],
                name=row["name"],
                unit=row["unit"],
                sort_order=(pos + 1) * 10,
            )
        )
        added += 1
    audit.record(session, actor, "count_item.load_defaults", "count_item", None, {"added": added})
    session.commit()
    return {"added": added, "skipped": len(data) - added}


# --- monthly sheets -------------------------------------------------------------------------


def _period_date(period: str) -> date:
    try:
        year, month = parse_period(period)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    return date(year, month, 1)


def _get_count(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: date, *, lock: bool = False
) -> MonthlyCount | None:
    stmt = select(MonthlyCount).where(
        MonthlyCount.tenant_id == actor.tenant_id,
        MonthlyCount.branch_id == branch_id,
        MonthlyCount.period == period,
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.scalar(stmt)


def _sync_draft(session: Session, count: MonthlyCount) -> None:
    """Keep a draft in step with the product list and with the previous closed month."""
    lines = {
        line.item_id: line
        for line in session.scalars(
            select(MonthlyCountLine).where(MonthlyCountLine.count_id == count.id)
        )
    }
    for item in session.scalars(
        select(CountItem).where(CountItem.tenant_id == count.tenant_id, CountItem.is_active)
    ):
        if item.id not in lines:
            line = MonthlyCountLine(count_id=count.id, item_id=item.id)
            session.add(line)
            lines[item.id] = line
    # Only the latest earlier sheet counts. If it was reopened (draft), skipping back to an
    # older closed month would silently replace the opening with stale numbers.
    latest = session.scalar(
        select(MonthlyCount)
        .where(MonthlyCount.branch_id == count.branch_id, MonthlyCount.period < count.period)
        .order_by(MonthlyCount.period.desc())
        .limit(1)
    )
    prev = latest if latest is not None and latest.status == "closed" else None
    prev_counted: dict[uuid.UUID, Decimal | None] = {}
    if prev is not None:
        prev_counted = {
            line.item_id: line.counted
            for line in session.scalars(
                select(MonthlyCountLine).where(MonthlyCountLine.count_id == prev.id)
            )
        }
    for item_id, line in lines.items():
        if prev is not None and item_id in prev_counted:
            line.opening = prev_counted[item_id] or Decimal("0")
            line.opening_locked = True
        elif line.opening_locked:
            # The month it came from was reopened: keep the value but allow editing.
            line.opening_locked = False
    session.flush()


def open_month(session: Session, actor: Actor, branch_id: uuid.UUID, period: str) -> MonthlyCount:
    """Returns the month's sheet, creating it as a draft on first access."""
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    p = _period_date(period)
    count = _get_count(session, actor, branch_id, p)
    if count is None:
        actor.require(Perm.STOCK_COUNT)
        get_active_branch(session, actor, branch_id)
        count = MonthlyCount(
            tenant_id=actor.tenant_id,
            branch_id=branch_id,
            period=p,
            status="draft",
            created_by=actor.user_id,
        )
        session.add(count)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            count = _get_count(session, actor, branch_id, p)
            if count is None:
                raise
        else:
            audit.record(
                session,
                actor,
                "monthly_count.open",
                "monthly_count",
                count.id,
                {"branch_id": str(branch_id), "period": period},
            )
    if count.status == "draft":
        _sync_draft(session, count)
    session.commit()
    return count


@dataclass(frozen=True)
class LineUpdate:
    item_id: uuid.UUID
    opening: Decimal | None = None
    incoming: Decimal | None = None
    used: Decimal | None = None
    counted: Decimal | None = None
    clear_counted: bool = False
    note: str | None = None


def _qty(value: Decimal, field: str) -> Decimal:
    if value < 0:
        raise Invalid(f"{field} negatif olamaz")
    if value > MAX_QTY:
        raise Invalid(f"{field} çok büyük")
    return value


def save_lines(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    period: str,
    updates: list[LineUpdate],
    header: dict[str, Any],
) -> MonthlyCount:
    actor.require(Perm.STOCK_COUNT)
    get_active_branch(session, actor, branch_id)
    count = _get_count(session, actor, branch_id, _period_date(period), lock=True)
    if count is None:
        raise NotFound("bu ay için sayım açılmamış")
    if count.status != "draft":
        raise Conflict("bu ayın sayımı kapatılmış; değişiklik için önce yeniden açılmalı")
    lines = {
        line.item_id: line
        for line in session.scalars(
            select(MonthlyCountLine).where(MonthlyCountLine.count_id == count.id)
        )
    }
    changed = 0
    for u in updates:
        line = lines.get(u.item_id)
        if line is None:
            raise Invalid(f"ürün {u.item_id} bu sayımda yok")
        if u.opening is not None:
            if line.opening_locked and u.opening != line.opening:
                raise Invalid("devir önceki kapanmış aydan geliyor ve değiştirilemez")
            line.opening = _qty(u.opening, "devir")
        if u.incoming is not None:
            line.incoming = _qty(u.incoming, "gelen")
        if u.used is not None:
            line.used = _qty(u.used, "satılan/kullanılan")
        if u.clear_counted:
            line.counted = None
        elif u.counted is not None:
            line.counted = _qty(u.counted, "sayılan")
        if u.note is not None:
            line.note = u.note.strip() or None
        changed += 1
    for key in ("counted_by_name", "count_date", "note"):
        if key in header:
            setattr(count, key, header[key])
    audit.record(
        session,
        actor,
        "monthly_count.save",
        "monthly_count",
        count.id,
        {"branch_id": str(branch_id), "period": period, "lines": changed},
    )
    session.commit()
    return count


def close_month(session: Session, actor: Actor, branch_id: uuid.UUID, period: str) -> MonthlyCount:
    actor.require(Perm.COUNT_CLOSE)
    get_branch(session, actor, branch_id)
    count = _get_count(session, actor, branch_id, _period_date(period), lock=True)
    if count is None:
        raise NotFound("bu ay için sayım açılmamış")
    if count.status == "closed":
        raise Conflict("bu ay zaten kapatılmış")
    _sync_draft(session, count)
    lines = list(
        session.scalars(select(MonthlyCountLine).where(MonthlyCountLine.count_id == count.id))
    )
    missing = [line for line in lines if line.counted is None]
    if missing:
        raise Invalid(
            f"{len(missing)} ürün sayılmadı. Kapatmak için her ürüne sayılan miktar girin "
            "(elde yoksa 0)."
        )
    if not count.counted_by_name:
        raise Invalid("sayımı yapanın adı girilmeli")
    prices = {
        i.id: i.unit_price
        for i in session.scalars(select(CountItem).where(CountItem.tenant_id == actor.tenant_id))
    }
    for line in lines:
        line.unit_price = prices.get(line.item_id)
    count.status = "closed"
    count.closed_by = actor.user_id
    count.closed_at = now()
    audit.record(
        session,
        actor,
        "monthly_count.close",
        "monthly_count",
        count.id,
        {"branch_id": str(branch_id), "period": period},
    )
    session.commit()
    return count


def reopen_month(
    session: Session, actor: Actor, branch_id: uuid.UUID, period: str, reason: str
) -> MonthlyCount:
    actor.require(Perm.COUNT_CLOSE)
    get_branch(session, actor, branch_id)
    if not reason.strip():
        raise Invalid("yeniden açma nedeni yazılmalı")
    p = _period_date(period)
    count = _get_count(session, actor, branch_id, p, lock=True)
    if count is None or count.status != "closed":
        raise Conflict("yalnızca kapatılmış bir ay yeniden açılabilir")
    later_closed = session.scalar(
        select(func.count())
        .select_from(MonthlyCount)
        .where(
            MonthlyCount.branch_id == branch_id,
            MonthlyCount.period > p,
            MonthlyCount.status == "closed",
        )
    )
    if later_closed:
        raise Conflict("sonraki aylardan biri kapatılmış; önce onu yeniden açın")
    count.status = "draft"
    count.closed_by = None
    count.closed_at = None
    audit.record(
        session,
        actor,
        "monthly_count.reopen",
        "monthly_count",
        count.id,
        {"branch_id": str(branch_id), "period": period, "reason": reason},
    )
    session.commit()
    return count


def list_months(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[MonthlyCount]:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    return list(
        session.scalars(
            select(MonthlyCount)
            .where(MonthlyCount.tenant_id == actor.tenant_id, MonthlyCount.branch_id == branch_id)
            .order_by(MonthlyCount.period.desc())
        )
    )


def sheet(session: Session, actor: Actor, count: MonthlyCount) -> dict[str, Any]:
    """The month with every line computed, grouped, plus totals."""
    rows = session.execute(
        select(MonthlyCountLine, CountItem)
        .join(CountItem, CountItem.id == MonthlyCountLine.item_id)
        .where(MonthlyCountLine.count_id == count.id)
        .order_by(CountItem.sort_order, CountItem.name)
    ).all()
    groups: dict[str, dict[str, Any]] = {}
    totals: dict[str, Any] = {
        "items": 0,
        "counted": 0,
        "fazla": 0,
        "eksik": 0,
        "tamam": 0,
        "fazla_value": Decimal("0"),
        "eksik_value": Decimal("0"),
        "stock_value": Decimal("0"),
        "priced_items": 0,
    }
    for line, item in rows:
        if count.status == "draft" and not item.is_active:
            continue
        price = line.unit_price if count.status == "closed" else item.unit_price
        r = compute_line(line.opening, line.incoming, line.used, line.counted, price)
        g = groups.setdefault(
            item.group_name,
            {"name": item.group_name, "lines": [], "difference_value": Decimal("0")},
        )
        g["lines"].append(
            {
                "item_id": item.id,
                "name": item.name,
                "unit": item.unit,
                "unit_price": price,
                "opening": line.opening,
                "opening_locked": line.opening_locked,
                "incoming": line.incoming,
                "used": line.used,
                "counted": line.counted,
                "expected": r.expected,
                "difference": r.difference,
                "status": r.status,
                "difference_value": r.difference_value,
                "counted_value": r.counted_value,
                "note": line.note,
            }
        )
        totals["items"] += 1
        if line.counted is not None:
            totals["counted"] += 1
            totals[r.status] += 1
        if price is not None:
            totals["priced_items"] += 1
        if r.difference_value is not None:
            g["difference_value"] += r.difference_value
            key = "fazla_value" if r.difference_value > 0 else "eksik_value"
            totals[key] += r.difference_value
        if r.counted_value is not None:
            totals["stock_value"] += r.counted_value
    totals["net_difference_value"] = totals["fazla_value"] + totals["eksik_value"]
    prev = session.scalar(
        select(MonthlyCount)
        .where(MonthlyCount.branch_id == count.branch_id, MonthlyCount.period < count.period)
        .order_by(MonthlyCount.period.desc())
        .limit(1)
    )
    return {
        "id": count.id,
        "branch_id": count.branch_id,
        "period": count.period.strftime("%Y-%m"),
        "status": count.status,
        "counted_by_name": count.counted_by_name,
        "count_date": count.count_date,
        "note": count.note,
        "closed_at": count.closed_at,
        "opening_from": (
            prev.period.strftime("%Y-%m") if prev is not None and prev.status == "closed" else None
        ),
        "groups": list(groups.values()),
        "totals": totals,
        # TL totals only cover priced products; say so instead of implying completeness.
        "value_coverage": f"{totals['priced_items']}/{totals['items']} üründe birim fiyat var",
    }
