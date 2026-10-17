"""Orders: idempotent creation, recipe-based stock consumption, cancellation with reversal.

Policy (ADR-0003): a sale has already happened in the real world, so sale consumption may
drive a balance negative instead of rejecting the order; the negative balance is surfaced
in reports. A product with no recipe valid at sale time is recorded with unknown cost.
"""

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.costing import money
from app.domain.permissions import Perm
from app.errors import Conflict, Invalid, NotFound
from app.models import (
    Order,
    OrderLine,
    RecipeLine,
    StockCount,
    StockCountLine,
    StockMovement,
)
from app.security import Actor
from app.services import audit
from app.services.catalog import recipe_at
from app.services.inventory import apply_movement, check_not_future, lock_balance, now
from app.services.scope import get_active_branch, get_branch, get_product


@dataclass(frozen=True)
class OrderLineIn:
    product_id: uuid.UUID
    quantity: int
    discount: Decimal = Decimal("0")


@dataclass(frozen=True)
class OrderIn:
    branch_id: uuid.UUID
    idempotency_key: str
    channel: str
    external_ref: str | None
    ordered_at: datetime | None
    lines: list[OrderLineIn]


def _request_hash(data: OrderIn) -> str:
    canonical = {
        "branch_id": str(data.branch_id),
        "channel": data.channel,
        "external_ref": data.external_ref,
        "ordered_at": data.ordered_at.isoformat() if data.ordered_at else None,
        "lines": [[str(x.product_id), x.quantity, str(x.discount)] for x in data.lines],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def _existing(session: Session, actor: Actor, key: str, request_hash: str) -> Order | None:
    order = session.scalar(
        select(Order).where(Order.tenant_id == actor.tenant_id, Order.idempotency_key == key)
    )
    if order is not None and order.request_hash != request_hash:
        raise Conflict("idempotency key was already used with a different request")
    return order


def create_order(session: Session, actor: Actor, data: OrderIn) -> tuple[Order, bool]:
    """Returns (order, created). A replay with the same key and body returns the original."""
    actor.require(Perm.ORDER_CREATE)
    get_active_branch(session, actor, data.branch_id)
    if not data.idempotency_key or len(data.idempotency_key) > 128:
        raise Invalid("idempotency key is required (max 128 chars)")
    if not data.lines:
        raise Invalid("order has no lines")
    request_hash = _request_hash(data)
    existing = _existing(session, actor, data.idempotency_key, request_hash)
    if existing is not None:
        return existing, False

    ordered_at = check_not_future(data.ordered_at or now())
    priced = []
    for line in data.lines:
        if line.quantity <= 0:
            raise Invalid("line quantity must be positive")
        if line.discount < 0:
            raise Invalid("discount cannot be negative")
        product = get_product(session, actor, line.product_id)
        gross = product.price * line.quantity
        if line.discount > gross:
            raise Invalid("discount exceeds line amount")
        priced.append((line, product, gross))

    order = Order(
        tenant_id=actor.tenant_id,
        branch_id=data.branch_id,
        idempotency_key=data.idempotency_key,
        request_hash=request_hash,
        channel=data.channel,
        external_ref=data.external_ref,
        status="completed",
        ordered_at=ordered_at,
        net_total=money(sum((g - ln.discount for ln, _, g in priced), Decimal("0"))),
        discount_total=money(sum((ln.discount for ln, _, _ in priced), Decimal("0"))),
        created_by=actor.user_id,
    )
    try:
        with session.begin_nested():
            session.add(order)
            session.flush()
    except IntegrityError:
        # A concurrent request with the same key committed first.
        existing = _existing(session, actor, data.idempotency_key, request_hash)
        if existing is None:
            raise
        session.commit()
        return existing, False

    # Resolve recipes and aggregate consumption per item.
    usage: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    line_usage: list[tuple[OrderLine, dict[uuid.UUID, Decimal] | None]] = []
    for line, product, gross in priced:
        recipe = recipe_at(session, product.id, ordered_at)
        order_line = OrderLine(
            order_id=order.id,
            product_id=product.id,
            quantity=line.quantity,
            unit_price=product.price,
            discount=line.discount,
            line_net=money(gross - line.discount),
            recipe_version_id=recipe.id if recipe else None,
        )
        per_item: dict[uuid.UUID, Decimal] | None = None
        if recipe is not None:
            per_item = {}
            for rl in session.scalars(
                select(RecipeLine).where(RecipeLine.recipe_version_id == recipe.id)
            ):
                amount = rl.gross_quantity_base * line.quantity
                per_item[rl.item_id] = per_item.get(rl.item_id, Decimal("0")) + amount
                usage[rl.item_id] += amount
        line_usage.append((order_line, per_item))

    avg_cost: dict[uuid.UUID, Decimal] = {}
    late_items: list[str] = []
    for item_id in sorted(usage):
        balance = lock_balance(session, actor.tenant_id, data.branch_id, item_id)
        avg_cost[item_id] = balance.avg_cost
        apply_movement(
            session,
            actor,
            balance,
            movement_type="sale_consumption",
            delta=-usage[item_id],
            occurred_at=ordered_at,
            allow_negative=True,
            ref_type="order",
            ref_id=order.id,
        )
        # FND-012: a count taken after the sale but before it was recorded already removed
        # this stock. Put it back at the count's time so the balance matches the physical
        # count and the variance report nets it against that count's adjustment.
        counted_after = last_count_after(session, data.branch_id, item_id, ordered_at)
        if counted_after is not None:
            late_items.append(str(item_id))
            apply_movement(
                session,
                actor,
                balance,
                movement_type="late_sale_offset",
                delta=usage[item_id],
                occurred_at=counted_after,
                allow_negative=True,
                inbound_unit_cost=avg_cost[item_id],
                ref_type="order",
                ref_id=order.id,
                note="sale dated before a posted count",
            )
    for order_line, per_item in line_usage:
        if per_item is not None:
            order_line.cost_total = money(
                sum((q * avg_cost[i] for i, q in per_item.items()), Decimal("0"))
            )
        session.add(order_line)

    audit.record(
        session,
        actor,
        "order.create",
        "order",
        order.id,
        {
            "branch_id": str(data.branch_id),
            "net_total": str(order.net_total),
            "lines": len(data.lines),
            "late_after_count_items": late_items,
        },
    )
    session.commit()
    return order, True


def last_count_after(
    session: Session, branch_id: uuid.UUID, item_id: uuid.UUID, at: datetime
) -> datetime | None:
    return session.scalar(
        select(func.max(StockCount.counted_at))
        .join(StockCountLine, StockCountLine.count_id == StockCount.id)
        .where(
            StockCount.branch_id == branch_id,
            StockCountLine.item_id == item_id,
            StockCount.counted_at > at,
        )
    )


def get_order(session: Session, actor: Actor, order_id: uuid.UUID, *, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id, Order.tenant_id == actor.tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    order = session.scalar(stmt)
    if order is None:
        raise NotFound("order not found")
    get_branch(session, actor, order.branch_id)  # branch scope check
    return order


def order_lines(session: Session, order_id: uuid.UUID) -> list[OrderLine]:
    return list(session.scalars(select(OrderLine).where(OrderLine.order_id == order_id)))


def cancel_order(session: Session, actor: Actor, order_id: uuid.UUID, reason: str) -> Order:
    """Cancel and put consumed stock back at the cost it left with. If food was already
    prepared, the kitchen records it separately as waste."""
    actor.require(Perm.ORDER_CANCEL)
    if not reason.strip():
        raise Invalid("cancel reason is required")
    order = get_order(session, actor, order_id, lock=True)
    if order.status == "cancelled":
        raise Conflict("order is already cancelled")
    movements = list(
        session.scalars(
            select(StockMovement)
            .where(
                StockMovement.ref_type == "order",
                StockMovement.ref_id == order.id,
                StockMovement.movement_type.in_(("sale_consumption", "late_sale_offset")),
            )
            .order_by(StockMovement.item_id, StockMovement.id)
        )
    )
    when = now()
    for movement in movements:
        balance = lock_balance(session, actor.tenant_id, order.branch_id, movement.item_id)
        if movement.movement_type == "sale_consumption":
            # Economic date of the reversal is the sale date, so period usage nets out.
            apply_movement(
                session,
                actor,
                balance,
                movement_type="sale_reversal",
                delta=-movement.quantity_base,
                occurred_at=order.ordered_at,
                allow_negative=True,
                inbound_unit_cost=movement.unit_cost,
                ref_type="order",
                ref_id=order.id,
                note=reason,
            )
        else:
            apply_movement(
                session,
                actor,
                balance,
                movement_type="late_sale_offset",
                delta=-movement.quantity_base,
                occurred_at=movement.occurred_at,
                allow_negative=True,
                ref_type="order",
                ref_id=order.id,
                note=reason,
            )
    order.status = "cancelled"
    order.cancelled_at = when
    order.cancel_reason = reason
    audit.record(
        session,
        actor,
        "order.cancel",
        "order",
        order.id,
        {"branch_id": str(order.branch_id), "reason": reason},
    )
    session.commit()
    return order
