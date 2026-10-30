"""Read-only views over monthly counts: month list with totals, dashboard, product history and
the all-branches overview. Nothing here creates or changes a sheet."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.monthly import compute_line
from app.domain.permissions import Perm
from app.errors import NotFound
from app.models import Branch, CountItem, MonthlyCount, MonthlyCountLine
from app.security import Actor
from app.services.monthly import _get_count, _period_date, sheet
from app.services.scope import get_branch

TOP_N = 10


def _months(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[MonthlyCount]:
    return list(
        session.scalars(
            select(MonthlyCount)
            .where(MonthlyCount.tenant_id == actor.tenant_id, MonthlyCount.branch_id == branch_id)
            .order_by(MonthlyCount.period.desc())
        )
    )


def _summary(session: Session, actor: Actor, count: MonthlyCount) -> dict[str, Any]:
    s = sheet(session, actor, count)
    return {
        "id": count.id,
        "period": s["period"],
        "status": count.status,
        "counted_by_name": count.counted_by_name,
        "count_date": count.count_date,
        "closed_at": count.closed_at,
        "totals": s["totals"],
    }


def month_summaries(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[dict[str, Any]]:
    actor.require(Perm.COUNT_READ)
    get_branch(session, actor, branch_id)
    return [_summary(session, actor, c) for c in _months(session, actor, branch_id)]


def _rank(lines: list[dict[str, Any]], key: str, value_key: str) -> list[dict[str, Any]]:
    """Largest first by TL when priced, otherwise by quantity (unpriced lines after priced)."""

    def sort_key(ln: dict[str, Any]) -> tuple[int, Decimal]:
        v = ln[value_key]
        return (0, -abs(v)) if v is not None else (1, -abs(ln[key]))

    return sorted(lines, key=sort_key)[:TOP_N]


def dashboard(session: Session, actor: Actor, branch_id: uuid.UUID, period: str) -> dict[str, Any]:
    actor.require(Perm.COUNT_READ)
    get_branch(session, actor, branch_id)
    count = _get_count(session, actor, branch_id, _period_date(period))
    months = _months(session, actor, branch_id)
    trend = [_summary(session, actor, c) for c in reversed(months[:12])]
    current = sheet(session, actor, count) if count is not None else None
    # Rankings come from the requested month, or the latest month with any counted line.
    focus = current
    if focus is None or focus["totals"]["counted"] == 0:
        for c in months:
            s = sheet(session, actor, c)
            if s["totals"]["counted"] > 0:
                focus = s
                break
    top_missing: list[dict[str, Any]] = []
    top_waste: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    if focus is not None:
        lines = [dict(ln, group=g["name"]) for g in focus["groups"] for ln in g["lines"]]
        top_missing = _rank(
            [ln for ln in lines if ln["status"] == "eksik"], "difference", "difference_value"
        )
        top_waste = _rank([ln for ln in lines if ln["waste"] > 0], "waste", "waste_value")
        for g in focus["groups"]:
            gl = g["lines"]
            groups.append(
                {
                    "name": g["name"],
                    "items": len(gl),
                    "counted": sum(1 for ln in gl if ln["counted"] is not None),
                    "eksik": sum(1 for ln in gl if ln["status"] == "eksik"),
                    "fazla": sum(1 for ln in gl if ln["status"] == "fazla"),
                    "difference_value": g["difference_value"],
                    "waste_value": sum(
                        (ln["waste_value"] for ln in gl if ln["waste_value"] is not None),
                        Decimal("0"),
                    ),
                }
            )
    return {
        "period": period,
        "current": None
        if current is None
        else {"status": current["status"], "totals": current["totals"]},
        "focus_period": focus["period"] if focus is not None else None,
        "trend": trend,
        "top_missing": top_missing,
        "top_waste": top_waste,
        "groups": groups,
    }


def item_history(
    session: Session, actor: Actor, branch_id: uuid.UUID, item_id: uuid.UUID
) -> dict[str, Any]:
    actor.require(Perm.COUNT_READ)
    get_branch(session, actor, branch_id)
    item = session.scalar(
        select(CountItem).where(CountItem.id == item_id, CountItem.tenant_id == actor.tenant_id)
    )
    if item is None:
        raise NotFound("ürün bulunamadı")
    rows = session.execute(
        select(MonthlyCountLine, MonthlyCount)
        .join(MonthlyCount, MonthlyCount.id == MonthlyCountLine.count_id)
        .where(MonthlyCount.branch_id == branch_id, MonthlyCountLine.item_id == item_id)
        .order_by(MonthlyCount.period)
    ).all()
    months = []
    for line, count in rows:
        price = line.unit_price if count.status == "closed" else item.unit_price
        r = compute_line(
            line.opening,
            line.incoming,
            line.used,
            line.counted,
            price,
            line.waste,
            line.recipe_used,
        )
        months.append(
            {
                "period": count.period.strftime("%Y-%m"),
                "status": count.status,
                "opening": line.opening,
                "incoming": line.incoming,
                "used": line.used,
                "recipe_used": line.recipe_used,
                "waste": line.waste,
                "expected": r.expected,
                "counted": line.counted,
                "difference": r.difference,
                "difference_status": r.status,
                "unit_price": price,
                "difference_value": r.difference_value,
                "waste_value": r.waste_value,
            }
        )
    return {
        "item": {
            "id": item.id,
            "name": item.name,
            "group_name": item.group_name,
            "unit": item.unit,
            "unit_price": item.unit_price,
        },
        "months": months,
    }


def overview(session: Session, actor: Actor, period: str) -> list[dict[str, Any]]:
    """Every branch the actor can see, with that month's status and totals."""
    actor.require(Perm.COUNT_READ)
    p = _period_date(period)
    stmt = select(Branch).where(Branch.tenant_id == actor.tenant_id, Branch.is_active)
    if actor.branch_ids is not None:
        stmt = stmt.where(Branch.id.in_(actor.branch_ids))
    out = []
    for branch in session.scalars(stmt.order_by(Branch.name)):
        count = _get_count(session, actor, branch.id, p)
        out.append(
            {
                "branch_id": branch.id,
                "branch_name": branch.name,
                "status": count.status if count is not None else "yok",
                "counted_by_name": count.counted_by_name if count is not None else None,
                "totals": sheet(session, actor, count)["totals"] if count is not None else None,
            }
        )
    return out
