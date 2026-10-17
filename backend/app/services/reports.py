"""Read-side reports. Unknown values are returned as None with a status, never as zero."""

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.domain.costing import gross_margin, money
from app.domain.permissions import Perm
from app.errors import Invalid
from app.models import (
    Item,
    Order,
    OrderLine,
    Product,
    StockBalance,
    StockCount,
    StockCountLine,
    StockMovement,
)
from app.security import Actor
from app.services.inventory import now
from app.services.scope import get_branch

ZERO = Decimal("0")


def _period(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise Invalid("period bounds must include a timezone")
    if end <= start:
        raise Invalid("period end must be after start")


def stock_on_hand(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[dict[str, Any]]:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    rows = session.execute(
        select(Item, StockBalance)
        .join(
            StockBalance,
            and_(StockBalance.item_id == Item.id, StockBalance.branch_id == branch_id),
            isouter=True,
        )
        .where(Item.tenant_id == actor.tenant_id)
        .order_by(Item.sku)
    ).all()
    out = []
    for item, bal in rows:
        quantity = bal.quantity if bal else None  # None: never moved in this branch
        out.append(
            {
                "item_id": item.id,
                "sku": item.sku,
                "name": item.name,
                "base_unit": item.base_unit,
                "is_active": item.is_active,
                "quantity": quantity,
                "avg_cost": bal.avg_cost if bal else None,
                "stock_value": money(bal.quantity * bal.avg_cost) if bal else None,
                "min_stock": item.min_stock,
                "below_min": (
                    None
                    if item.min_stock is None or quantity is None
                    else quantity < item.min_stock
                ),
                "negative": quantity is not None and quantity < 0,
            }
        )
    return out


def variance(
    session: Session, actor: Actor, branch_id: uuid.UUID, start: datetime, end: datetime
) -> dict[str, Any]:
    """Theoretical (recipe) usage vs. recorded waste vs. unexplained count differences.

    actual usage    = theoretical + recorded waste + unexplained
    unexplained     = -(sum of count adjustments in the period)
    Unexplained is None for items without a posted count in the period: without a count we
    do not know the actual usage, and reporting 0 would claim a perfect match.
    """
    actor.require(Perm.REPORT_READ)
    get_branch(session, actor, branch_id)
    _period(start, end)
    rows = session.execute(
        select(
            StockMovement.item_id,
            StockMovement.movement_type,
            StockMovement.waste_reason,
            func.sum(StockMovement.quantity_base),
            func.sum(StockMovement.total_cost),
        )
        .where(
            StockMovement.tenant_id == actor.tenant_id,
            StockMovement.branch_id == branch_id,
            StockMovement.occurred_at >= start,
            StockMovement.occurred_at < end,
        )
        .group_by(StockMovement.item_id, StockMovement.movement_type, StockMovement.waste_reason)
    ).all()
    counted = set(
        session.scalars(
            select(StockCountLine.item_id)
            .join(StockCount, StockCount.id == StockCountLine.count_id)
            .where(
                StockCount.branch_id == branch_id,
                StockCount.counted_at >= start,
                StockCount.counted_at < end,
            )
        )
    )
    agg: dict[uuid.UUID, dict[str, Any]] = defaultdict(
        lambda: {
            "theoretical_qty": ZERO,
            "theoretical_cost": ZERO,
            "waste_qty": ZERO,
            "waste_cost": ZERO,
            "waste_by_reason": {},
            "adjust_qty": ZERO,
            "adjust_cost": ZERO,
        }
    )
    for item_id, mtype, reason, q, c in rows:
        a = agg[item_id]
        if mtype in ("sale_consumption", "sale_reversal"):
            a["theoretical_qty"] -= q
            a["theoretical_cost"] -= c
        elif mtype == "waste":
            a["waste_qty"] -= q
            a["waste_cost"] -= c
            a["waste_by_reason"][reason] = str(-q)
        elif mtype == "count_adjustment":
            a["adjust_qty"] += q
            a["adjust_cost"] += c
    items = {
        i.id: i for i in session.scalars(select(Item).where(Item.tenant_id == actor.tenant_id))
    }
    lines = []
    for item_id in sorted(set(agg) | counted, key=lambda i: items[i].sku):
        a = agg[item_id]
        has_count = item_id in counted
        unexplained_qty = -a["adjust_qty"] if has_count else None
        unexplained_cost = money(-a["adjust_cost"]) if has_count else None
        lines.append(
            {
                "item_id": item_id,
                "sku": items[item_id].sku,
                "base_unit": items[item_id].base_unit,
                "theoretical_qty": a["theoretical_qty"],
                "theoretical_cost": money(a["theoretical_cost"]),
                "recorded_waste_qty": a["waste_qty"],
                "recorded_waste_cost": money(a["waste_cost"]),
                "waste_by_reason": a["waste_by_reason"],
                "has_count_in_period": has_count,
                "unexplained_qty": unexplained_qty,
                "unexplained_cost": unexplained_cost,
                "actual_qty": (
                    a["theoretical_qty"] + a["waste_qty"] + unexplained_qty
                    if unexplained_qty is not None
                    else None
                ),
                # Not labelled as theft or loss: it needs review (period cut-off, transfers,
                # late postings can all produce differences).
                "status": "needs_review"
                if unexplained_qty
                else ("ok" if has_count else "no_count_in_period"),
            }
        )
    return {
        "branch_id": branch_id,
        "period_start": start,
        "period_end": end,
        "generated_at": now(),
        "source": "stock_movements (occurred_at in period)",
        "lines": lines,
    }


def margin(
    session: Session, actor: Actor, branch_id: uuid.UUID, start: datetime, end: datetime
) -> dict[str, Any]:
    actor.require(Perm.REPORT_READ)
    get_branch(session, actor, branch_id)
    _period(start, end)
    rows = session.execute(
        select(
            Product.id,
            Product.sku,
            Product.name,
            func.sum(OrderLine.quantity),
            func.sum(OrderLine.line_net),
            func.sum(OrderLine.discount),
            func.sum(OrderLine.cost_total),
            func.count().filter(OrderLine.cost_total.is_(None)),
        )
        .join(OrderLine, OrderLine.product_id == Product.id)
        .join(Order, Order.id == OrderLine.order_id)
        .where(
            Order.tenant_id == actor.tenant_id,
            Order.branch_id == branch_id,
            Order.status == "completed",
            Order.ordered_at >= start,
            Order.ordered_at < end,
        )
        .group_by(Product.id, Product.sku, Product.name)
        .order_by(Product.sku)
    ).all()
    lines = []
    total_rev, total_cogs, any_missing = ZERO, ZERO, False
    for pid, sku, name, q, rev, disc, cogs, missing in rows:
        cost = None if missing else cogs
        m = gross_margin(rev, cost)
        total_rev += rev
        if cost is None:
            any_missing = True
        else:
            total_cogs += cost
        lines.append(
            {
                "product_id": pid,
                "sku": sku,
                "name": name,
                "quantity": int(q),
                "net_revenue": m.net_revenue,
                "discounts": money(disc),
                "cogs": m.cogs,
                "gross_profit": m.gross_profit,
                "margin_pct": m.margin_pct,
                "status": m.status,
                "lines_without_recipe": int(missing),
            }
        )
    total = gross_margin(total_rev, None if any_missing else total_cogs)
    return {
        "branch_id": branch_id,
        "period_start": start,
        "period_end": end,
        "generated_at": now(),
        "source": "orders/order_lines (completed, ordered_at in period); prices net of VAT",
        "lines": lines,
        "total": {
            "net_revenue": total.net_revenue,
            "cogs": total.cogs,
            "gross_profit": total.gross_profit,
            "margin_pct": total.margin_pct,
            "status": total.status,
        },
    }
