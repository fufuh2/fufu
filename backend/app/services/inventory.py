"""Stock ledger operations.

Concurrency model: every change to a (branch, item) balance happens under a row lock
(SELECT ... FOR UPDATE) in the same transaction that appends the ledger row. Multi-item
operations lock in item-id order to avoid deadlocks.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.costing import money, moving_average_cost, qty, unit_cost
from app.domain.permissions import Perm
from app.domain.units import ConversionError, to_base
from app.errors import InsufficientStock, Invalid
from app.models import (
    WASTE_REASONS,
    Item,
    ItemUnitConversion,
    StockBalance,
    StockCount,
    StockCountLine,
    StockMovement,
)
from app.security import Actor
from app.services import audit
from app.services.scope import get_active_branch, get_item

FUTURE_TOLERANCE = timedelta(minutes=5)


def now() -> datetime:
    return datetime.now(UTC)


def check_not_future(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise Invalid("timestamps must include a timezone")
    if ts > now() + FUTURE_TOLERANCE:
        raise Invalid("timestamp is in the future")
    return ts


def convert(session: Session, item: Item, quantity: Decimal, unit: str) -> Decimal:
    conversions = {
        c.unit: c.factor_to_base
        for c in session.scalars(
            select(ItemUnitConversion).where(ItemUnitConversion.item_id == item.id)
        )
    }
    try:
        base = qty(to_base(quantity, unit, item.base_unit, conversions))
    except ConversionError as exc:
        raise Invalid(f"{item.sku}: {exc}") from exc
    if quantity > 0 and base <= 0:
        # e.g. 0.0001 of a 0.0001 g unit: below the ledger's 4-decimal precision
        raise Invalid(f"{item.sku}: quantity is below the smallest storable amount")
    return base


def check_after_last_count(
    session: Session, branch_id: uuid.UUID, item_id: uuid.UUID, occurred_at: datetime
) -> None:
    """A posted count already reflects every physical movement before it. Recording a
    movement dated before that count would apply it a second time."""
    last = session.scalar(
        select(func.max(StockCount.counted_at))
        .join(StockCountLine, StockCountLine.count_id == StockCount.id)
        .where(StockCount.branch_id == branch_id, StockCountLine.item_id == item_id)
    )
    if last is not None and occurred_at < last:
        raise Invalid(
            "occurred_at is before the last stock count for this item; record the "
            "difference with a new count instead"
        )


def lock_balance(
    session: Session, tenant_id: uuid.UUID, branch_id: uuid.UUID, item_id: uuid.UUID
) -> StockBalance:
    session.execute(
        pg_insert(StockBalance)
        .values(
            branch_id=branch_id,
            item_id=item_id,
            tenant_id=tenant_id,
            quantity=Decimal("0"),
            avg_cost=Decimal("0"),
        )
        .on_conflict_do_nothing()
    )
    balance = session.scalar(
        select(StockBalance)
        .where(StockBalance.branch_id == branch_id, StockBalance.item_id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if balance is None:  # pragma: no cover - the upsert above guarantees the row
        raise RuntimeError("stock balance row missing after upsert")
    return balance


def apply_movement(
    session: Session,
    actor: Actor,
    balance: StockBalance,
    *,
    movement_type: str,
    delta: Decimal,
    occurred_at: datetime,
    allow_negative: bool,
    inbound_unit_cost: Decimal | None = None,
    waste_reason: str | None = None,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    note: str | None = None,
) -> StockMovement:
    """Append one ledger row and update the locked balance. Caller must hold the lock."""
    if delta == 0:
        raise Invalid("quantity must not be zero")
    if delta > 0:
        if inbound_unit_cost is None:
            raise Invalid("inbound movement requires a unit cost")
        cost = unit_cost(inbound_unit_cost)
        balance.avg_cost = moving_average_cost(balance.quantity, balance.avg_cost, delta, cost)
    else:
        cost = balance.avg_cost
    new_qty = balance.quantity + delta
    if new_qty < 0 and not allow_negative:
        raise InsufficientStock(
            f"insufficient stock for item {balance.item_id}: on hand {balance.quantity}, "
            f"requested {-delta}"
        )
    balance.quantity = new_qty
    balance.updated_at = now()
    movement = StockMovement(
        tenant_id=actor.tenant_id,
        branch_id=balance.branch_id,
        item_id=balance.item_id,
        movement_type=movement_type,
        quantity_base=delta,
        unit_cost=cost,
        total_cost=money(delta * cost),
        balance_after=new_qty,
        waste_reason=waste_reason,
        ref_type=ref_type,
        ref_id=ref_id,
        note=note,
        occurred_at=occurred_at,
        user_id=actor.user_id,
    )
    session.add(movement)
    return movement


@dataclass(frozen=True)
class ReceiptLine:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str
    unit_cost: Decimal  # per entered unit, net of VAT


def book_receipt(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    lines: list[ReceiptLine],
    when: datetime,
    *,
    ref_type: str,
    ref_id: uuid.UUID,
    note: str | None,
) -> list[StockMovement]:
    """Validate and post receipt lines without committing (callers own the transaction).
    Returned movements follow the order of `lines`."""
    if not lines:
        raise Invalid("receipt has no lines")
    prepared = []
    for idx, line in enumerate(lines):
        if line.quantity <= 0:
            raise Invalid("received quantity must be positive")
        if line.unit_cost < 0:
            raise Invalid("unit cost cannot be negative")
        item = get_item(session, actor, line.item_id)
        base_qty = convert(session, item, line.quantity, line.unit)
        per_base = line.unit_cost * line.quantity / base_qty
        prepared.append((item.id, idx, base_qty, per_base))
    movements: dict[int, StockMovement] = {}
    for item_id, idx, base_qty, per_base in sorted(prepared, key=lambda p: (p[0], p[1])):
        balance = lock_balance(session, actor.tenant_id, branch_id, item_id)
        check_after_last_count(session, branch_id, item_id, when)
        movements[idx] = apply_movement(
            session,
            actor,
            balance,
            movement_type="receipt",
            delta=base_qty,
            occurred_at=when,
            allow_negative=True,
            inbound_unit_cost=per_base,
            ref_type=ref_type,
            ref_id=ref_id,
            note=note,
        )
    return [movements[i] for i in range(len(lines))]


def receive(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    lines: list[ReceiptLine],
    supplier: str | None,
    occurred_at: datetime | None,
) -> list[StockMovement]:
    """Receipt without a purchase order (e.g. market purchase)."""
    actor.require(Perm.STOCK_RECEIVE)
    get_active_branch(session, actor, branch_id)
    when = check_not_future(occurred_at or now())
    receipt_id = uuid.uuid4()
    movements = book_receipt(
        session, actor, branch_id, lines, when, ref_type="receipt", ref_id=receipt_id, note=supplier
    )
    audit.record(
        session,
        actor,
        "stock.receive",
        "receipt",
        receipt_id,
        {"branch_id": str(branch_id), "lines": len(lines), "supplier": supplier},
    )
    session.commit()
    return movements


def record_waste(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    item_id: uuid.UUID,
    quantity: Decimal,
    unit: str,
    reason: str,
    note: str | None,
    occurred_at: datetime | None,
) -> StockMovement:
    actor.require(Perm.STOCK_WASTE)
    get_active_branch(session, actor, branch_id)
    if reason not in WASTE_REASONS:
        raise Invalid(f"unknown waste reason '{reason}'")
    if quantity <= 0:
        raise Invalid("waste quantity must be positive")
    item = get_item(session, actor, item_id)
    base_qty = convert(session, item, quantity, unit)
    when = check_not_future(occurred_at or now())
    balance = lock_balance(session, actor.tenant_id, branch_id, item.id)
    # Checked under the balance lock so a concurrent count cannot slip in between.
    check_after_last_count(session, branch_id, item.id, when)
    movement = apply_movement(
        session,
        actor,
        balance,
        movement_type="waste",
        delta=-base_qty,
        occurred_at=when,
        allow_negative=False,
        waste_reason=reason,
        note=note,
    )
    session.flush()
    audit.record(
        session,
        actor,
        "stock.waste",
        "stock_movement",
        movement.id,
        {
            "branch_id": str(branch_id),
            "item_id": str(item.id),
            "quantity_base": str(base_qty),
            "reason": reason,
        },
    )
    session.commit()
    return movement


@dataclass(frozen=True)
class CountLine:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str


def post_count(
    session: Session, actor: Actor, branch_id: uuid.UUID, lines: list[CountLine]
) -> StockCount:
    """Physical count, posted immediately at server time. Differences become
    count_adjustment movements valued at the current average cost."""
    actor.require(Perm.STOCK_COUNT)
    get_active_branch(session, actor, branch_id)
    if not lines:
        raise Invalid("count has no lines")
    if len({line.item_id for line in lines}) != len(lines):
        raise Invalid("an item appears more than once in the count")
    prepared = []
    for line in lines:
        if line.quantity < 0:
            raise Invalid("counted quantity cannot be negative")
        item = get_item(session, actor, line.item_id, active=False)
        prepared.append((item.id, convert(session, item, line.quantity, line.unit)))
    when = now()
    count = StockCount(
        tenant_id=actor.tenant_id, branch_id=branch_id, counted_at=when, created_by=actor.user_id
    )
    session.add(count)
    session.flush()
    for item_id, counted in sorted(prepared, key=lambda p: p[0]):
        balance = lock_balance(session, actor.tenant_id, branch_id, item_id)
        system_qty = balance.quantity
        diff = counted - system_qty
        session.add(
            StockCountLine(
                count_id=count.id,
                item_id=item_id,
                counted_quantity_base=counted,
                system_quantity_base=system_qty,
                difference_base=diff,
            )
        )
        if diff != 0:
            apply_movement(
                session,
                actor,
                balance,
                movement_type="count_adjustment",
                delta=diff,
                occurred_at=when,
                allow_negative=False,
                # A count surplus is valued at the current average; nothing was bought.
                inbound_unit_cost=balance.avg_cost if diff > 0 else None,
                ref_type="stock_count",
                ref_id=count.id,
            )
    audit.record(
        session,
        actor,
        "stock.count",
        "stock_count",
        count.id,
        {"branch_id": str(branch_id), "lines": len(lines)},
    )
    session.commit()
    return count
