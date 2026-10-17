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
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    min_stock: Mapped[Decimal | None] = mapped_column(QTY)
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
