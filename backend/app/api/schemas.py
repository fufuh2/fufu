import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.permissions import Role

# Bounded so absurd values are rejected before they reach NUMERIC(18,4) columns.
Qty = Annotated[Decimal, Field(gt=0, le=Decimal("1000000000"), decimal_places=4)]
NonNegQty = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"), decimal_places=4)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"), decimal_places=6)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Code = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]
UnitCode = Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[a-z0-9_]+$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=256)]


class PasswordChangeIn(Strict):
    current_password: Annotated[str, Field(min_length=1, max_length=256)]
    new_password: Annotated[str, Field(min_length=12, max_length=256)]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret


class BranchIn(Strict):
    name: Name


class BranchPatch(Strict):
    name: Name | None = None
    is_active: bool | None = None


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    is_active: bool


class UserIn(Strict):
    email: EmailStr
    password: Annotated[str, Field(min_length=12, max_length=256)]
    role: Role
    branch_ids: list[uuid.UUID] = []


class UserPatch(Strict):
    role: Role | None = None
    branch_ids: list[uuid.UUID] | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    role: str
    is_active: bool


class ItemIn(Strict):
    sku: Code
    name: Name
    base_unit: Annotated[str, Field(pattern=r"^(g|ml|pcs)$")]
    min_stock: NonNegQty | None = None
    allergens: list[Annotated[str, Field(min_length=1, max_length=64)]] = []
    conversions: dict[UnitCode, Qty] = {}


class ItemPatch(Strict):
    name: Name | None = None
    min_stock: NonNegQty | None = None  # explicit null clears the minimum
    allergens: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = None
    is_active: bool | None = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sku: str
    name: str
    base_unit: str
    min_stock: Decimal | None
    allergens: list[str]
    is_active: bool


class ProductIn(Strict):
    sku: Code
    name: Name
    price: Annotated[Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2)]


class ProductPatch(Strict):
    name: Name | None = None
    price: Annotated[Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2)] | None = None
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sku: str
    name: str
    price: Decimal
    is_active: bool


class RecipeLineSchema(Strict):
    item_id: uuid.UUID
    quantity: Qty
    unit: UnitCode
    loss_pct: Annotated[Decimal, Field(ge=0, lt=100, decimal_places=2)] = Decimal("0")


class RecipeIn(Strict):
    valid_from: datetime | None = None
    lines: Annotated[list[RecipeLineSchema], Field(min_length=1, max_length=200)]


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    version: int
    valid_from: datetime
    valid_to: datetime | None


class ReceiptLineSchema(Strict):
    item_id: uuid.UUID
    quantity: Qty
    unit: UnitCode
    unit_cost: Money


class ReceiptIn(Strict):
    supplier: Annotated[str, Field(max_length=200)] | None = None
    occurred_at: datetime | None = None
    lines: Annotated[list[ReceiptLineSchema], Field(min_length=1, max_length=500)]


class WasteIn(Strict):
    item_id: uuid.UUID
    quantity: Qty
    unit: UnitCode
    reason: str
    note: Annotated[str, Field(max_length=500)] | None = None
    occurred_at: datetime | None = None


class CountLineSchema(Strict):
    item_id: uuid.UUID
    quantity: NonNegQty
    unit: UnitCode


class CountIn(Strict):
    lines: Annotated[list[CountLineSchema], Field(min_length=1, max_length=2000)]


class CountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    counted_at: datetime


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_id: uuid.UUID
    movement_type: str
    quantity_base: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    balance_after: Decimal
    waste_reason: str | None
    ref_type: str | None
    ref_id: uuid.UUID | None
    occurred_at: datetime
    recorded_at: datetime
    user_id: uuid.UUID


class OrderLineSchema(Strict):
    product_id: uuid.UUID
    quantity: Annotated[int, Field(gt=0, le=10000)]
    discount: Annotated[Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2)] = Decimal(
        "0"
    )


class OrderIn(Strict):
    channel: Annotated[str, Field(pattern=r"^(pos|online|manual|test)$")] = "manual"
    external_ref: Annotated[str, Field(max_length=128)] | None = None
    ordered_at: datetime | None = None
    lines: Annotated[list[OrderLineSchema], Field(min_length=1, max_length=200)]


class OrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    discount: Decimal
    line_net: Decimal
    recipe_version_id: uuid.UUID | None
    cost_total: Decimal | None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    status: str
    channel: str
    external_ref: str | None
    ordered_at: datetime
    net_total: Decimal
    discount_total: Decimal
    lines: list[OrderLineOut] = []


class CancelIn(Strict):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    data: dict
    at: datetime
