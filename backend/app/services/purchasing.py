"""Suppliers, purchase orders, receipts against orders, invoice matching, reorder suggestions.

The system never sends anything to a supplier: "approved" only records an internal decision.
Orders above the tenant's approval limit need an approver other than the creator.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.costing import money, unit_cost
from app.domain.permissions import Perm
from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import (
    Item,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseOrderLine,
    StockBalance,
    StockMovement,
    Supplier,
    SupplierPrice,
    Tenant,
)
from app.security import Actor
from app.services import audit
from app.services.inventory import ReceiptLine, book_receipt, check_not_future, convert, now
from app.services.scope import get_active_branch, get_branch, get_item

PRICE_VARIANCE_PCT = Decimal("5")
OPEN_STATUSES = ("pending_approval", "approved", "partially_received")


# --- suppliers ------------------------------------------------------------------------------


def create_supplier(
    session: Session,
    actor: Actor,
    *,
    name: str,
    tax_id: str | None,
    email: str | None,
    phone: str | None,
) -> Supplier:
    actor.require(Perm.PURCHASE_MANAGE)
    supplier = Supplier(
        tenant_id=actor.tenant_id, name=name, tax_id=tax_id, email=email, phone=phone
    )
    session.add(supplier)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("supplier name already exists") from exc
    audit.record(session, actor, "supplier.create", "supplier", supplier.id, {"name": name})
    session.commit()
    return supplier


def get_supplier(
    session: Session, actor: Actor, supplier_id: uuid.UUID, *, active: bool = True
) -> Supplier:
    supplier = session.scalar(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == actor.tenant_id)
    )
    if supplier is None or (active and not supplier.is_active):
        raise NotFound("supplier not found or inactive")
    return supplier


def update_supplier(
    session: Session, actor: Actor, supplier_id: uuid.UUID, fields: dict[str, Any]
) -> Supplier:
    actor.require(Perm.PURCHASE_MANAGE)
    supplier = get_supplier(session, actor, supplier_id, active=False)
    before = {k: getattr(supplier, k) for k in fields}
    for key, value in fields.items():
        setattr(supplier, key, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("supplier name already exists") from exc
    audit.record(
        session,
        actor,
        "supplier.update",
        "supplier",
        supplier.id,
        {
            "before": {k: str(v) for k, v in before.items()},
            "after": {k: str(v) for k, v in fields.items()},
        },
    )
    session.commit()
    return supplier


def list_suppliers(session: Session, actor: Actor) -> list[Supplier]:
    actor.require(Perm.STOCK_READ)
    return list(
        session.scalars(
            select(Supplier).where(Supplier.tenant_id == actor.tenant_id).order_by(Supplier.name)
        )
    )


def record_quote(
    session: Session,
    actor: Actor,
    supplier_id: uuid.UUID,
    item_id: uuid.UUID,
    price: Decimal,
    unit: str,
) -> SupplierPrice:
    """A quoted price per entered unit, stored per base unit in the price history."""
    actor.require(Perm.PURCHASE_MANAGE)
    supplier = get_supplier(session, actor, supplier_id)
    item = get_item(session, actor, item_id)
    if price < 0:
        raise Invalid("price cannot be negative")
    per_base = unit_cost(price / convert(session, item, Decimal("1"), unit))
    row = SupplierPrice(
        supplier_id=supplier.id, item_id=item.id, unit_price=per_base, source="quote"
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        actor,
        "supplier.quote",
        "supplier",
        supplier.id,
        {"item_id": str(item.id), "unit_price_base": str(per_base)},
    )
    session.commit()
    return row


def price_history(
    session: Session, actor: Actor, supplier_id: uuid.UUID, item_id: uuid.UUID
) -> list[SupplierPrice]:
    actor.require(Perm.STOCK_READ)
    get_supplier(session, actor, supplier_id, active=False)
    get_item(session, actor, item_id, active=False)
    return list(
        session.scalars(
            select(SupplierPrice)
            .where(SupplierPrice.supplier_id == supplier_id, SupplierPrice.item_id == item_id)
            .order_by(SupplierPrice.recorded_at, SupplierPrice.id)
        )
    )


# --- purchase orders ------------------------------------------------------------------------


@dataclass(frozen=True)
class POLineIn:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str
    unit_price: Decimal  # per entered unit, net of VAT


def create_po(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    supplier_id: uuid.UUID,
    lines: list[POLineIn],
    expected_at: datetime | None,
    note: str | None,
) -> PurchaseOrder:
    actor.require(Perm.PURCHASE_MANAGE)
    get_active_branch(session, actor, branch_id)
    supplier = get_supplier(session, actor, supplier_id)
    if not lines:
        raise Invalid("purchase order has no lines")
    if len({line.item_id for line in lines}) != len(lines):
        raise Invalid("an item appears more than once in the order")
    if expected_at is not None and expected_at.tzinfo is None:
        raise Invalid("timestamps must include a timezone")
    po = PurchaseOrder(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        supplier_id=supplier.id,
        status="draft",
        total=Decimal("0"),
        expected_at=expected_at,
        note=note,
        created_by=actor.user_id,
    )
    session.add(po)
    session.flush()
    total = Decimal("0")
    for line in lines:
        if line.quantity <= 0:
            raise Invalid("ordered quantity must be positive")
        if line.unit_price < 0:
            raise Invalid("price cannot be negative")
        item = get_item(session, actor, line.item_id)
        base = convert(session, item, line.quantity, line.unit)
        session.add(
            PurchaseOrderLine(
                purchase_order_id=po.id,
                item_id=item.id,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                quantity_base=base,
            )
        )
        total += line.quantity * line.unit_price
    po.total = money(total)
    audit.record(
        session,
        actor,
        "po.create",
        "purchase_order",
        po.id,
        {"branch_id": str(branch_id), "supplier_id": str(supplier.id), "total": str(po.total)},
    )
    session.commit()
    return po


def get_po(
    session: Session, actor: Actor, po_id: uuid.UUID, *, lock: bool = False
) -> PurchaseOrder:
    stmt = select(PurchaseOrder).where(
        PurchaseOrder.id == po_id, PurchaseOrder.tenant_id == actor.tenant_id
    )
    if lock:
        stmt = stmt.with_for_update()
    po = session.scalar(stmt)
    if po is None:
        raise NotFound("purchase order not found")
    get_branch(session, actor, po.branch_id)
    return po


def po_lines(session: Session, po_id: uuid.UUID) -> list[PurchaseOrderLine]:
    return list(
        session.scalars(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.purchase_order_id == po_id)
            .order_by(PurchaseOrderLine.id)
        )
    )


def _transition(
    session: Session, actor: Actor, po: PurchaseOrder, to: str, data: dict[str, Any]
) -> PurchaseOrder:
    before = po.status
    po.status = to
    audit.record(
        session,
        actor,
        f"po.{to}",
        "purchase_order",
        po.id,
        {"branch_id": str(po.branch_id), "from": before, **data},
    )
    session.commit()
    return po


def submit_po(session: Session, actor: Actor, po_id: uuid.UUID) -> PurchaseOrder:
    actor.require(Perm.PURCHASE_MANAGE)
    po = get_po(session, actor, po_id, lock=True)
    if po.status != "draft":
        raise Conflict(f"only a draft can be submitted (status: {po.status})")
    return _transition(session, actor, po, "pending_approval", {})


def approve_po(session: Session, actor: Actor, po_id: uuid.UUID) -> PurchaseOrder:
    actor.require(Perm.PURCHASE_APPROVE)
    po = get_po(session, actor, po_id, lock=True)
    if po.status != "pending_approval":
        raise Conflict(f"only a submitted order can be approved (status: {po.status})")
    limit = session.scalar(select(Tenant.po_approval_limit).where(Tenant.id == actor.tenant_id))
    if limit is not None and po.total > limit and po.created_by == actor.user_id:
        raise Forbidden(f"orders above {limit} need approval by someone other than their creator")
    po.approved_by = actor.user_id
    po.approved_at = now()
    return _transition(session, actor, po, "approved", {"total": str(po.total)})


def cancel_po(session: Session, actor: Actor, po_id: uuid.UUID, reason: str) -> PurchaseOrder:
    actor.require(Perm.PURCHASE_MANAGE)
    po = get_po(session, actor, po_id, lock=True)
    if po.status not in ("draft", "pending_approval", "approved"):
        raise Conflict(f"cannot cancel an order in status {po.status}; close it instead")
    po.closed_reason = reason
    return _transition(session, actor, po, "cancelled", {"reason": reason})


def close_po(session: Session, actor: Actor, po_id: uuid.UUID, reason: str) -> PurchaseOrder:
    """Stop expecting the remaining quantities of a partially received order."""
    actor.require(Perm.PURCHASE_MANAGE)
    po = get_po(session, actor, po_id, lock=True)
    if po.status != "partially_received":
        raise Conflict(f"only a partially received order can be closed (status: {po.status})")
    po.closed_reason = reason
    return _transition(session, actor, po, "closed", {"reason": reason})


@dataclass(frozen=True)
class POReceiveLineIn:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str
    unit_price: Decimal | None  # actual price per entered unit; None = the ordered price


def receive_po(
    session: Session,
    actor: Actor,
    po_id: uuid.UUID,
    lines: list[POReceiveLineIn],
    occurred_at: datetime | None,
) -> dict[str, Any]:
    actor.require(Perm.STOCK_RECEIVE)
    po = get_po(session, actor, po_id, lock=True)
    get_active_branch(session, actor, po.branch_id)
    if po.status not in ("approved", "partially_received"):
        raise Conflict(f"cannot receive against an order in status {po.status}")
    if not lines:
        raise Invalid("receipt has no lines")
    if len({line.item_id for line in lines}) != len(lines):
        raise Invalid("an item appears more than once in the receipt")
    when = check_not_future(occurred_at or now())
    ordered = {line.item_id: line for line in po_lines(session, po.id)}
    receipt_lines: list[ReceiptLine] = []
    variances: list[dict[str, str | None]] = []
    for line in lines:
        pol = ordered.get(line.item_id)
        if pol is None:
            raise Invalid(f"item {line.item_id} is not on this purchase order")
        if line.quantity <= 0:
            raise Invalid("received quantity must be positive")
        if line.unit_price is not None and line.unit_price < 0:
            raise Invalid("price cannot be negative")
        item = get_item(session, actor, line.item_id)
        base = convert(session, item, line.quantity, line.unit)
        remaining = pol.quantity_base - pol.received_quantity_base
        if base > remaining:
            raise Invalid(
                f"{item.sku}: receiving {base} exceeds the open quantity {remaining}; "
                "book the surplus as a separate receipt"
            )
        ordered_per_base = pol.unit_price * pol.quantity / pol.quantity_base
        actual_per_base = (
            ordered_per_base if line.unit_price is None else line.unit_price * line.quantity / base
        )
        if ordered_per_base > 0:
            pct = (actual_per_base - ordered_per_base) / ordered_per_base * 100
            if abs(pct) > PRICE_VARIANCE_PCT:
                variances.append(
                    {
                        "item_id": str(item.id),
                        "ordered_unit_price_base": str(unit_cost(ordered_per_base)),
                        "actual_unit_price_base": str(unit_cost(actual_per_base)),
                        "difference_pct": str(pct.quantize(Decimal("0.01"))),
                    }
                )
        elif actual_per_base > 0:
            variances.append(
                {
                    "item_id": str(item.id),
                    "ordered_unit_price_base": "0",
                    "actual_unit_price_base": str(unit_cost(actual_per_base)),
                    "difference_pct": None,
                }
            )
        pol.received_quantity_base += base
        receipt_lines.append(ReceiptLine(item.id, base, item.base_unit, actual_per_base))
        session.add(
            SupplierPrice(
                supplier_id=po.supplier_id,
                item_id=item.id,
                unit_price=unit_cost(actual_per_base),
                source="receipt",
            )
        )
    movements = book_receipt(
        session,
        actor,
        po.branch_id,
        receipt_lines,
        when,
        ref_type="purchase_order",
        ref_id=po.id,
        note=None,
    )
    complete = all(p.received_quantity_base >= p.quantity_base for p in ordered.values())
    po.status = "received" if complete else "partially_received"
    audit.record(
        session,
        actor,
        "po.receive",
        "purchase_order",
        po.id,
        {
            "branch_id": str(po.branch_id),
            "lines": len(lines),
            "status": po.status,
            "price_variances": variances,
        },
    )
    if variances:
        audit.record(
            session,
            actor,
            "purchase.price_variance",
            "purchase_order",
            po.id,
            {"branch_id": str(po.branch_id), "variances": variances},
        )
    session.commit()
    return {
        "purchase_order_id": po.id,
        "status": po.status,
        "received_value": money(sum((m.total_cost for m in movements), Decimal("0"))),
        "price_variances": variances,
    }


def record_invoice(
    session: Session, actor: Actor, po_id: uuid.UUID, invoice_no: str, amount: Decimal
) -> PurchaseInvoice:
    """Three-way check lite: invoice amount vs. the value actually received for the order."""
    actor.require(Perm.PURCHASE_MANAGE)
    po = get_po(session, actor, po_id, lock=True)
    if po.status not in ("partially_received", "received", "closed"):
        raise Conflict("nothing has been received against this order yet")
    if amount < 0:
        raise Invalid("invoice amount cannot be negative")
    received_value = money(
        session.scalar(
            select(func.coalesce(func.sum(StockMovement.total_cost), 0)).where(
                StockMovement.ref_type == "purchase_order",
                StockMovement.ref_id == po.id,
                StockMovement.movement_type == "receipt",
            )
        )
        or Decimal("0")
    )
    already = session.scalar(
        select(func.coalesce(func.sum(PurchaseInvoice.amount), 0)).where(
            PurchaseInvoice.purchase_order_id == po.id
        )
    ) or Decimal("0")
    difference = money(already + amount - received_value)
    invoice = PurchaseInvoice(
        tenant_id=actor.tenant_id,
        purchase_order_id=po.id,
        invoice_no=invoice_no,
        amount=money(amount),
        received_value=received_value,
        difference=difference,
        status="matched" if abs(difference) <= Decimal("0.01") else "mismatch",
        recorded_by=actor.user_id,
    )
    session.add(invoice)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("invoice number already recorded for this order") from exc
    audit.record(
        session,
        actor,
        "po.invoice",
        "purchase_order",
        po.id,
        {
            "branch_id": str(po.branch_id),
            "invoice_no": invoice_no,
            "amount": str(amount),
            "difference": str(difference),
            "status": invoice.status,
        },
    )
    session.commit()
    return invoice


def list_pos(
    session: Session, actor: Actor, branch_id: uuid.UUID, status: str | None
) -> list[PurchaseOrder]:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    stmt = (
        select(PurchaseOrder)
        .where(PurchaseOrder.tenant_id == actor.tenant_id, PurchaseOrder.branch_id == branch_id)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(200)
    )
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    return list(session.scalars(stmt))


def suggestions(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[dict[str, Any]]:
    """Reorder suggestions: items whose on-hand plus open orders is below the minimum.
    Suggested quantity tops up to the target (or the minimum when no target is set)."""
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    items = session.scalars(
        select(Item)
        .where(Item.tenant_id == actor.tenant_id, Item.is_active, Item.min_stock.is_not(None))
        .order_by(Item.sku)
    )
    out = []
    for item in items:
        bal = session.get(StockBalance, (branch_id, item.id))
        on_hand = bal.quantity if bal else Decimal("0")
        open_qty = session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        PurchaseOrderLine.quantity_base - PurchaseOrderLine.received_quantity_base
                    ),
                    0,
                )
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(
                PurchaseOrder.branch_id == branch_id,
                PurchaseOrder.status.in_(OPEN_STATUSES),
                PurchaseOrderLine.item_id == item.id,
            )
        ) or Decimal("0")
        if item.min_stock is None or on_hand + open_qty >= item.min_stock:
            continue
        goal = item.target_stock if item.target_stock is not None else item.min_stock
        last = session.execute(
            select(SupplierPrice.supplier_id, SupplierPrice.unit_price)
            .join(Supplier, Supplier.id == SupplierPrice.supplier_id)
            .where(SupplierPrice.item_id == item.id, Supplier.is_active)
            .order_by(SupplierPrice.recorded_at.desc(), SupplierPrice.id.desc())
            .limit(1)
        ).first()
        out.append(
            {
                "item_id": item.id,
                "sku": item.sku,
                "base_unit": item.base_unit,
                "on_hand": on_hand,
                "open_order_quantity": open_qty,
                "min_stock": item.min_stock,
                "target_stock": item.target_stock,
                "suggested_quantity": goal - on_hand - open_qty,
                "last_supplier_id": last[0] if last else None,
                "last_unit_price_base": last[1] if last else None,
            }
        )
    return out
