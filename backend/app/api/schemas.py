import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.permissions import Role

Name = Annotated[str, Field(min_length=1, max_length=200)]
Password = Annotated[str, Field(min_length=12, max_length=256)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=256)]


class PasswordChangeIn(Strict):
    current_password: Annotated[str, Field(min_length=1, max_length=256)]
    new_password: Password


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
    password: Password
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


class UserListOut(UserOut):
    branch_ids: list[str]
    created_at: datetime


class PasswordResetIn(Strict):
    new_password: Password


class CancelIn(Strict):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    user_email: str | None = None
    data: dict
    at: datetime


CountQty = Annotated[Decimal, Field(ge=0, le=Decimal("99999999999.999"), decimal_places=3)]
CountUnit = Annotated[str, Field(pattern="^(KG|ADET|LT|PKT|GR|ML|KOLİ)$")]
Period = Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]
PeriodQuery = Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]


Content = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=4)]


class CountItemIn(Strict):
    group_name: Annotated[str, Field(min_length=1, max_length=100)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    unit: CountUnit
    unit_price: (
        Annotated[Decimal, Field(ge=0, le=Decimal("100000000"), decimal_places=2)] | None
    ) = None
    content_qty: Content | None = None
    content_unit: CountUnit | None = None


class CountItemPatch(Strict):
    group_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    unit: CountUnit | None = None
    unit_price: (
        Annotated[Decimal, Field(ge=0, le=Decimal("100000000"), decimal_places=2)] | None
    ) = None
    sort_order: Annotated[int, Field(ge=0, le=1000000)] | None = None
    is_active: bool | None = None
    content_qty: Content | None = None
    content_unit: CountUnit | None = None


class CountItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    group_name: str
    name: str
    unit: str
    unit_price: Decimal | None
    sort_order: int
    is_active: bool
    content_qty: Decimal | None
    content_unit: str | None


class CountLineIn(Strict):
    item_id: uuid.UUID
    opening: CountQty | None = None
    incoming: CountQty | None = None
    used: CountQty | None = None
    waste: CountQty | None = None
    counted: CountQty | None = None
    clear_counted: bool = False
    note: Annotated[str, Field(max_length=300)] | None = None


class MonthlySaveIn(Strict):
    counted_by_name: Annotated[str, Field(max_length=200)] | None = None
    count_date: date | None = None
    note: Annotated[str, Field(max_length=1000)] | None = None
    lines: Annotated[list[CountLineIn], Field(max_length=2000)] = []
    sales: Annotated[list["SaleIn"], Field(max_length=2000)] = []
    # The sheet version the edits were made on; omit to save without the check.
    base_version: Annotated[int, Field(ge=0)] | None = None


class SaleIn(Strict):
    product_id: uuid.UUID
    quantity: CountQty


MenuPrice = Annotated[Decimal, Field(ge=0, le=Decimal("1000000"), decimal_places=2)]


class MenuProductIn(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    price: MenuPrice | None = None


class MenuProductPatch(Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    price: MenuPrice | None = None
    is_active: bool | None = None
    sort_order: Annotated[int, Field(ge=0, le=1000000)] | None = None


class RecipeLineIn(Strict):
    item_id: uuid.UUID
    quantity: Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=4)]
    unit: CountUnit


class RecipeIn(Strict):
    lines: Annotated[list[RecipeLineIn], Field(max_length=200)]
