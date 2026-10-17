import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

QTY = Numeric(18, 4)
UNIT_COST = Numeric(18, 6)
MONEY = Numeric(18, 2)


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="TRY")
    # Purchase orders above this total need approval by someone other than their creator.
    po_approval_limit: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("10000"), server_default="10000"
    )
    created_at: Mapped[datetime] = _now()


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Bumped to revoke every token issued for this user (logout-all, password or role change).
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = _now()


class UserBranchAccess(Base):
    __tablename__ = "user_branch_access"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), primary_key=True)


class Item(Base):
    """Raw material, semi-finished good or packaging held in stock."""

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku"),
        CheckConstraint("base_unit IN ('g','ml','pcs')", name="ck_items_base_unit"),
        CheckConstraint("min_stock IS NULL OR min_stock >= 0", name="ck_items_min_stock"),
        CheckConstraint(
            "target_stock IS NULL OR (target_stock >= 0 AND "
            "(min_stock IS NULL OR target_stock >= min_stock))",
            name="ck_items_target_stock",
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    min_stock: Mapped[Decimal | None] = mapped_column(QTY)
    target_stock: Mapped[Decimal | None] = mapped_column(QTY)
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class ItemUnitConversion(Base):
    __tablename__ = "item_unit_conversions"
    __table_args__ = (CheckConstraint("factor_to_base > 0", name="ck_iuc_factor_positive"),)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), primary_key=True)
    unit: Mapped[str] = mapped_column(String(16), primary_key=True)
    factor_to_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)


class Product(Base):
    """Sellable menu product. Price is net of VAT (VAT handling is out of phase-1 scope)."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku"),
        CheckConstraint("price >= 0", name="ck_products_price"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class RecipeVersion(Base):
    __tablename__ = "recipe_versions"
    __table_args__ = (
        UniqueConstraint("product_id", "version"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_recipe_validity"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class RecipeLine(Base):
    __tablename__ = "recipe_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_recipe_line_qty"),
        CheckConstraint("loss_pct >= 0 AND loss_pct < 100", name="ck_recipe_line_loss"),
        CheckConstraint("gross_quantity_base > 0", name="ck_recipe_line_gross"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recipe_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipe_versions.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    loss_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    gross_quantity_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)


class StockBalance(Base):
    """Materialised running balance; the ledger (stock_movements) is the source of truth."""

    __tablename__ = "stock_balances"
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal("0"))
    avg_cost: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = _now()


MOVEMENT_TYPES = (
    "receipt",
    "sale_consumption",
    "sale_reversal",
    "waste",
    "count_adjustment",
    # +qty when a sale dated before a posted count arrives after it (the count already
    # removed that stock); occurred_at is the count's time so it nets with that count.
    "late_sale_offset",
    "transfer_out",
    "transfer_in",
)
WASTE_REASONS = (
    "spoilage_expiry",
    "prep_loss",
    "cooking_loss",
    "production_error",
    "customer_return",
    "staff_meal",
    "spill_accident",
    "transit_loss",
    "unknown",
)


class StockMovement(Base):
    """Append-only stock ledger. UPDATE/DELETE are rejected by a database trigger."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('" + "','".join(MOVEMENT_TYPES) + "')", name="ck_movement_type"
        ),
        CheckConstraint("quantity_base <> 0", name="ck_movement_qty_nonzero"),
        CheckConstraint(
            "(movement_type = 'waste') = (waste_reason IS NOT NULL)",
            name="ck_movement_waste_reason",
        ),
        CheckConstraint(
            "waste_reason IS NULL OR waste_reason IN ('" + "','".join(WASTE_REASONS) + "')",
            name="ck_movement_waste_reason_values",
        ),
        CheckConstraint("unit_cost >= 0", name="ck_movement_unit_cost"),
        Index("ix_movements_branch_occurred", "branch_id", "occurred_at"),
        Index("ix_movements_ref", "ref_type", "ref_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    waste_reason: Mapped[str | None] = mapped_column(String(32))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = _now()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_orders_idempotency"),
        CheckConstraint("status IN ('completed','cancelled')", name="ck_order_status"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    net_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _now()


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_line_qty"),
        CheckConstraint(
            "discount >= 0 AND discount <= unit_price * quantity", name="ck_order_line_disc"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_net: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # NULL recipe/cost means "no recipe was valid at sale time": cost is unknown, not zero.
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_versions.id"))
    cost_total: Mapped[Decimal | None] = mapped_column(MONEY)


class StockCount(Base):
    __tablename__ = "stock_counts"
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"
    __table_args__ = (
        UniqueConstraint("count_id", "item_id"),
        CheckConstraint("counted_quantity_base >= 0", name="ck_count_line_qty"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    count_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_counts.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), nullable=False)
    counted_quantity_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    system_quantity_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    difference_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)


class AuditEvent(Base):
    """Append-only audit trail. UPDATE/DELETE are rejected by a database trigger."""

    __tablename__ = "audit_events"
    __table_args__ = (
        # Serves the login failure limit (services/admin.authenticate).
        Index(
            "ix_audit_login_failed",
            text("(data ->> 'email')"),
            "at",
            postgresql_where=text("action = 'auth.login_failed'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    at: Mapped[datetime] = _now()


class Transfer(Base):
    """Two-step transfer: shipped (stock left the source, in transit) -> received/cancelled."""

    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("from_branch_id <> to_branch_id", name="ck_transfer_branches"),
        CheckConstraint("status IN ('shipped','received','cancelled')", name="ck_transfer_status"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    from_branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    to_branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    shipped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shipped_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class TransferLine(Base):
    __tablename__ = "transfer_lines"
    __table_args__ = (
        UniqueConstraint("transfer_id", "item_id"),
        CheckConstraint("shipped_quantity_base > 0", name="ck_transfer_line_shipped"),
        CheckConstraint(
            "received_quantity_base IS NULL OR "
            "(received_quantity_base >= 0 AND received_quantity_base <= shipped_quantity_base)",
            name="ck_transfer_line_received",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transfers.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), nullable=False)
    shipped_quantity_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)
    received_quantity_base: Mapped[Decimal | None] = mapped_column(QTY)


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class SupplierPrice(Base):
    """Append-only price history per supplier and item (price per base unit)."""

    __tablename__ = "supplier_prices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # quote | receipt
    recorded_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="ck_supplier_price"),
        CheckConstraint("source IN ('quote','receipt')", name="ck_supplier_price_source"),
        Index("ix_supplier_prices_lookup", "supplier_id", "item_id", "recorded_at"),
    )


PO_STATUSES = (
    "draft",
    "pending_approval",
    "approved",
    "partially_received",
    "received",
    "closed",
    "cancelled",
)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint("status IN ('" + "','".join(PO_STATUSES) + "')", name="ck_po_status"),
        CheckConstraint("total >= 0", name="ck_po_total"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _now()


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "item_id"),
        CheckConstraint("quantity > 0", name="ck_pol_qty"),
        CheckConstraint("quantity_base > 0", name="ck_pol_qty_base"),
        CheckConstraint("unit_price >= 0", name="ck_pol_price"),
        CheckConstraint("received_quantity_base >= 0", name="ck_pol_received"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)  # per entered unit
    quantity_base: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    received_quantity_base: Mapped[Decimal] = mapped_column(
        QTY, nullable=False, default=Decimal("0")
    )


class PurchaseInvoice(Base):
    __tablename__ = "purchase_invoices"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "invoice_no"),
        CheckConstraint("amount >= 0", name="ck_invoice_amount"),
        CheckConstraint("status IN ('matched','mismatch')", name="ck_invoice_status"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=False, index=True
    )
    invoice_no: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    received_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    difference: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    recorded_at: Mapped[datetime] = _now()


# --- quality --------------------------------------------------------------------------------

CHECKLIST_KINDS = ("opening", "closing", "hygiene", "temperature", "equipment", "oil", "expiry")


class ChecklistTemplate(Base):
    __tablename__ = "checklist_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint(
            "kind IN ('" + "','".join(CHECKLIST_KINDS) + "')", name="ck_checklist_kind"
        ),
        CheckConstraint("frequency IN ('daily','adhoc')", name="ck_checklist_frequency"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    __table_args__ = (
        CheckConstraint("answer_type IN ('check','number')", name="ck_checklist_item_type"),
        CheckConstraint(
            "answer_type = 'number' OR (min_value IS NULL AND max_value IS NULL)",
            name="ck_checklist_item_range_only_numbers",
        ),
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_checklist_item_range",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklist_templates.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    answer_type: Mapped[str] = mapped_column(String(8), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16))
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    evidence_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ChecklistRun(Base):
    """A completed checklist. Append-only (DB trigger): a correction is a new run that
    references the one it corrects, with a reason."""

    __tablename__ = "checklist_runs"
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklist_templates.id"), nullable=False
    )
    performed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    corrects_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checklist_runs.id"))
    correction_reason: Mapped[str | None] = mapped_column(String(500))
    recorded_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint(
            "(corrects_run_id IS NULL) = (correction_reason IS NULL)",
            name="ck_checklist_run_correction",
        ),
        Index("ix_checklist_runs_branch_time", "branch_id", "performed_at"),
    )


class ChecklistAnswer(Base):
    __tablename__ = "checklist_answers"
    __table_args__ = (
        UniqueConstraint("run_id", "item_id"),
        CheckConstraint(
            "(checked IS NULL) <> (value IS NULL)", name="ck_checklist_answer_one_value"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklist_runs.id"), nullable=False, index=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("checklist_items.id"), nullable=False)
    checked: Mapped[bool | None] = mapped_column(Boolean)
    value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(String(500))


class Nonconformity(Base):
    __tablename__ = "nonconformities"
    __table_args__ = (
        CheckConstraint("status IN ('open','resolved')", name="ck_nc_status"),
        CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL AND corrective_action IS NOT NULL)",
            name="ck_nc_resolution",
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    answer_id: Mapped[int | None] = mapped_column(ForeignKey("checklist_answers.id"))
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    corrective_action: Mapped[str | None] = mapped_column(String(1000))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()


# --- workforce ------------------------------------------------------------------------------


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_shift_times"),
        CheckConstraint("ends_at - starts_at <= interval '24 hours'", name="ck_shift_length"),
        Index("ix_shifts_user_time", "user_id", "starts_at"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        CheckConstraint(
            "clock_out_at IS NULL OR clock_out_at > clock_in_at", name="ck_attendance_times"
        ),
        # At most one open clock-in per user.
        Index(
            "uq_attendance_open",
            "user_id",
            unique=True,
            postgresql_where=text("clock_out_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    clock_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clock_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('open','done','cancelled')", name="ck_task_status"),
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)", name="ck_task_completion"
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class HandoverNote(Base):
    """Shift handover. Append-only (DB trigger)."""

    __tablename__ = "handover_notes"
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    note: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = _now()
