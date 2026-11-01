import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

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
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner','branch_manager','counter','viewer')", name="ck_user_role"
        ),
    )
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


# --- monthly inventory count ---------------------------------------------------------------

COUNT_UNITS = ("KG", "ADET", "LT", "PKT", "GR", "ML", "KOLİ")


class CountItem(Base):
    """A product on the monthly count sheet (grouped like the restaurant's paper form)."""

    __tablename__ = "count_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint("unit IN ('" + "','".join(COUNT_UNITS) + "')", name="ck_count_item_unit"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="ck_count_item_price"),
        CheckConstraint(
            "(content_qty IS NULL AND content_unit IS NULL) OR "
            "(content_qty > 0 AND content_unit IN ('" + "','".join(COUNT_UNITS) + "'))",
            name="ck_count_item_content",
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    group_name: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Package content for items counted per package: 1 ADET (kova) = 9.5 KG. Lets recipes in
    # grams consume items counted in pieces.
    content_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    content_unit: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = _now()


class MonthlyCount(Base):
    __tablename__ = "monthly_counts"
    __table_args__ = (
        UniqueConstraint("branch_id", "period"),
        CheckConstraint("status IN ('draft','closed')", name="ck_monthly_count_status"),
        CheckConstraint("extract(day from period) = 1", name="ck_monthly_count_period_first_day"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    period: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    counted_by_name: Mapped[str | None] = mapped_column(String(200))
    count_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class MonthlyCountLine(Base):
    __tablename__ = "monthly_count_lines"
    __table_args__ = (
        UniqueConstraint("count_id", "item_id"),
        CheckConstraint("opening >= 0", name="ck_mcl_opening"),
        CheckConstraint("incoming >= 0", name="ck_mcl_incoming"),
        CheckConstraint("used >= 0", name="ck_mcl_used"),
        CheckConstraint("waste >= 0", name="ck_mcl_waste"),
        CheckConstraint("recipe_used >= 0", name="ck_mcl_recipe_used"),
        CheckConstraint("counted IS NULL OR counted >= 0", name="ck_mcl_counted"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    count_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monthly_counts.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("count_items.id"), nullable=False)
    opening: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    # True when the opening came from the previous closed month and may not be edited.
    opening_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    incoming: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    used: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    # Zayi: spoiled, spilled or thrown away. Deducted from expected, valued separately.
    waste: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, default=Decimal("0"), server_default="0"
    )
    # Sold menu products × recipe, recomputed while the month is a draft and frozen at close.
    recipe_used: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, default=Decimal("0"), server_default="0"
    )
    counted: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    # Price snapshot at close, so later price edits do not rewrite a closed month's value.
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    note: Mapped[str | None] = mapped_column(String(300))


# --- menu products and recipes -------------------------------------------------------------


class MenuProduct(Base):
    """Something sold (a burger, a menu); its recipe says what it uses from the count list."""

    __tablename__ = "menu_products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_menu_product_price"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Menu (sales) price, used for the food cost ratio.
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    created_at: Mapped[datetime] = _now()


class RecipeLine(Base):
    __tablename__ = "recipe_lines"
    __table_args__ = (
        UniqueConstraint("product_id", "item_id"),
        CheckConstraint("quantity > 0", name="ck_recipe_line_qty"),
        CheckConstraint("unit IN ('" + "','".join(COUNT_UNITS) + "')", name="ck_recipe_line_unit"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_products.id"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("count_items.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)


class MonthlySale(Base):
    """How many of a menu product the branch sold in that month."""

    __tablename__ = "monthly_sales"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_monthly_sale_qty"),)
    count_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monthly_counts.id"), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("menu_products.id"), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
