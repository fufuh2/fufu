"""Inter-branch transfers (two-step).

ship:    transfer_out at the source's average cost; cannot overdraw the source.
receive: transfer_in at the destination with the shipped cost; any shortfall is recorded at
         the destination as waste with reason 'transit_loss', so it is visible and costed.
cancel:  before receipt only; the stock goes back to the source at the shipped cost.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.permissions import Perm
from app.errors import Conflict, Invalid, NotFound
from app.models import Branch, Transfer, TransferLine
from app.security import Actor
from app.services import audit
from app.services.inventory import (
    apply_movement,
    check_after_last_count,
    convert,
    lock_balance,
    now,
)
from app.services.scope import get_active_branch, get_branch, get_item


@dataclass(frozen=True)
class TransferLineIn:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class ReceiveLineIn:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str


def ship(
    session: Session,
    actor: Actor,
    from_branch_id: uuid.UUID,
    to_branch_id: uuid.UUID,
    lines: list[TransferLineIn],
    note: str | None,
) -> Transfer:
    actor.require(Perm.STOCK_TRANSFER)
    get_active_branch(session, actor, from_branch_id)
    # The destination only has to exist in the tenant: the sender may not work there.
    _tenant_branch(session, actor, to_branch_id)
    if from_branch_id == to_branch_id:
        raise Invalid("source and destination must differ")
    if not lines:
        raise Invalid("transfer has no lines")
    if len({line.item_id for line in lines}) != len(lines):
        raise Invalid("an item appears more than once in the transfer")
    prepared = []
    for line in lines:
        if line.quantity <= 0:
            raise Invalid("transfer quantity must be positive")
        item = get_item(session, actor, line.item_id)
        prepared.append((item.id, convert(session, item, line.quantity, line.unit)))
    when = now()
    transfer = Transfer(
        tenant_id=actor.tenant_id,
        from_branch_id=from_branch_id,
        to_branch_id=to_branch_id,
        status="shipped",
        note=note,
        shipped_at=when,
        shipped_by=actor.user_id,
    )
    session.add(transfer)
    session.flush()
    for item_id, qty in sorted(prepared, key=lambda p: p[0]):
        balance = lock_balance(session, actor.tenant_id, from_branch_id, item_id)
        movement = apply_movement(
            session,
            actor,
            balance,
            movement_type="transfer_out",
            delta=-qty,
            occurred_at=when,
            allow_negative=False,
            ref_type="transfer",
            ref_id=transfer.id,
        )
        session.add(
            TransferLine(
                transfer_id=transfer.id,
                item_id=item_id,
                shipped_quantity_base=qty,
                unit_cost=movement.unit_cost,
            )
        )
    audit.record(
        session,
        actor,
        "transfer.ship",
        "transfer",
        transfer.id,
        {"branch_id": str(from_branch_id), "to_branch_id": str(to_branch_id), "lines": len(lines)},
    )
    session.commit()
    return transfer


def _tenant_branch(session: Session, actor: Actor, branch_id: uuid.UUID) -> None:
    branch = session.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.tenant_id == actor.tenant_id)
    )
    if branch is None or not branch.is_active:
        raise NotFound("destination branch not found or inactive")


def get_transfer(
    session: Session, actor: Actor, transfer_id: uuid.UUID, *, lock: bool = False
) -> Transfer:
    stmt = select(Transfer).where(Transfer.id == transfer_id, Transfer.tenant_id == actor.tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    transfer = session.scalar(stmt)
    if transfer is None:
        raise NotFound("transfer not found")
    # Visible to users of either branch.
    if actor.branch_ids is not None and not (
        {transfer.from_branch_id, transfer.to_branch_id} & actor.branch_ids
    ):
        raise NotFound("transfer not found")
    return transfer


def transfer_lines(session: Session, transfer_id: uuid.UUID) -> list[TransferLine]:
    return list(
        session.scalars(
            select(TransferLine)
            .where(TransferLine.transfer_id == transfer_id)
            .order_by(TransferLine.item_id)
        )
    )


def receive(
    session: Session,
    actor: Actor,
    transfer_id: uuid.UUID,
    received: list[ReceiveLineIn] | None,
) -> Transfer:
    """`received=None` means everything arrived as shipped."""
    actor.require(Perm.STOCK_RECEIVE)
    transfer = get_transfer(session, actor, transfer_id, lock=True)
    get_active_branch(session, actor, transfer.to_branch_id)
    if transfer.status != "shipped":
        raise Conflict(f"transfer is already {transfer.status}")
    lines = transfer_lines(session, transfer.id)
    by_item = {line.item_id: line for line in lines}
    got: dict[uuid.UUID, Decimal] = {}
    if received is None:
        got = {line.item_id: line.shipped_quantity_base for line in lines}
    else:
        for r in received:
            if r.item_id not in by_item:
                raise Invalid(f"item {r.item_id} is not on this transfer")
            if r.item_id in got:
                raise Invalid("an item appears more than once")
            if r.quantity < 0:
                raise Invalid("received quantity cannot be negative")
            item = get_item(session, actor, r.item_id, active=False)
            qty = convert(session, item, r.quantity, r.unit) if r.quantity > 0 else Decimal("0")
            if qty > by_item[r.item_id].shipped_quantity_base:
                raise Invalid("received more than was shipped; record the surplus as a receipt")
            got[r.item_id] = qty
        missing = set(by_item) - set(got)
        if missing:
            raise Invalid("every shipped item needs a received quantity (0 if nothing arrived)")
    when = now()
    for line in lines:
        balance = lock_balance(session, actor.tenant_id, transfer.to_branch_id, line.item_id)
        check_after_last_count(session, transfer.to_branch_id, line.item_id, when)
        line.received_quantity_base = got[line.item_id]
        # Book the full shipment in, then the shortfall out as transit loss: both the
        # transfer and the loss are visible in the destination's ledger at the same cost.
        apply_movement(
            session,
            actor,
            balance,
            movement_type="transfer_in",
            delta=line.shipped_quantity_base,
            occurred_at=when,
            allow_negative=True,
            inbound_unit_cost=line.unit_cost,
            ref_type="transfer",
            ref_id=transfer.id,
        )
        shortfall = line.shipped_quantity_base - got[line.item_id]
        if shortfall > 0:
            apply_movement(
                session,
                actor,
                balance,
                movement_type="waste",
                delta=-shortfall,
                occurred_at=when,
                allow_negative=True,
                waste_reason="transit_loss",
                ref_type="transfer",
                ref_id=transfer.id,
            )
    transfer.status = "received"
    transfer.closed_at = when
    transfer.closed_by = actor.user_id
    audit.record(
        session,
        actor,
        "transfer.receive",
        "transfer",
        transfer.id,
        {
            "branch_id": str(transfer.to_branch_id),
            "shortfall_items": [
                str(i) for i, q in got.items() if q < by_item[i].shipped_quantity_base
            ],
        },
    )
    session.commit()
    return transfer


def cancel(session: Session, actor: Actor, transfer_id: uuid.UUID, reason: str) -> Transfer:
    actor.require(Perm.STOCK_TRANSFER)
    if not reason.strip():
        raise Invalid("cancel reason is required")
    transfer = get_transfer(session, actor, transfer_id, lock=True)
    get_branch(session, actor, transfer.from_branch_id)
    if transfer.status != "shipped":
        raise Conflict(f"transfer is already {transfer.status}")
    when = now()
    for line in transfer_lines(session, transfer.id):
        balance = lock_balance(session, actor.tenant_id, transfer.from_branch_id, line.item_id)
        apply_movement(
            session,
            actor,
            balance,
            movement_type="transfer_in",
            delta=line.shipped_quantity_base,
            occurred_at=when,
            allow_negative=True,
            inbound_unit_cost=line.unit_cost,
            ref_type="transfer",
            ref_id=transfer.id,
            note=f"cancelled: {reason}",
        )
    transfer.status = "cancelled"
    transfer.closed_at = when
    transfer.closed_by = actor.user_id
    audit.record(
        session,
        actor,
        "transfer.cancel",
        "transfer",
        transfer.id,
        {"branch_id": str(transfer.from_branch_id), "reason": reason},
    )
    session.commit()
    return transfer


def list_transfers(
    session: Session, actor: Actor, branch_id: uuid.UUID, status: str | None
) -> list[Transfer]:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    stmt = (
        select(Transfer)
        .where(
            Transfer.tenant_id == actor.tenant_id,
            (Transfer.from_branch_id == branch_id) | (Transfer.to_branch_id == branch_id),
        )
        .order_by(Transfer.shipped_at.desc())
        .limit(200)
    )
    if status is not None:
        stmt = stmt.where(Transfer.status == status)
    return list(session.scalars(stmt))
