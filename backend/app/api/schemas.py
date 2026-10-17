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
    target_stock: NonNegQty | None = None
    allergens: list[Annotated[str, Field(min_length=1, max_length=64)]] = []
    conversions: dict[UnitCode, Qty] = {}


class ItemPatch(Strict):
    name: Name | None = None
    min_stock: NonNegQty | None = None  # explicit null clears the minimum
    target_stock: NonNegQty | None = None
    allergens: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = None
    is_active: bool | None = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sku: str
    name: str
    base_unit: str
    min_stock: Decimal | None
    target_stock: Decimal | None
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


class TransferLineSchema(Strict):
    item_id: uuid.UUID
    quantity: Qty
    unit: UnitCode


class TransferIn(Strict):
    to_branch_id: uuid.UUID
    note: Annotated[str, Field(max_length=500)] | None = None
    lines: Annotated[list[TransferLineSchema], Field(min_length=1, max_length=500)]


class TransferReceiveLine(Strict):
    item_id: uuid.UUID
    quantity: NonNegQty
    unit: UnitCode


class TransferReceiveIn(Strict):
    # Omit to receive everything exactly as shipped.
    lines: Annotated[list[TransferReceiveLine], Field(max_length=500)] | None = None


class TransferLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: uuid.UUID
    shipped_quantity_base: Decimal
    unit_cost: Decimal
    received_quantity_base: Decimal | None


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_branch_id: uuid.UUID
    to_branch_id: uuid.UUID
    status: str
    note: str | None
    shipped_at: datetime
    closed_at: datetime | None
    lines: list[TransferLineOut] = []


Price = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"), decimal_places=6)]


class SupplierIn(Strict):
    name: Name
    tax_id: Annotated[str, Field(max_length=32)] | None = None
    email: EmailStr | None = None
    phone: Annotated[str, Field(max_length=32)] | None = None


class SupplierPatch(Strict):
    name: Name | None = None
    tax_id: Annotated[str, Field(max_length=32)] | None = None
    email: EmailStr | None = None
    phone: Annotated[str, Field(max_length=32)] | None = None
    is_active: bool | None = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    tax_id: str | None
    email: str | None
    phone: str | None
    is_active: bool


class QuoteIn(Strict):
    item_id: uuid.UUID
    unit_price: Price
    unit: UnitCode


class SupplierPriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    supplier_id: uuid.UUID
    item_id: uuid.UUID
    unit_price: Decimal
    source: str
    recorded_at: datetime


class POLineSchema(Strict):
    item_id: uuid.UUID
    quantity: Qty
    unit: UnitCode
    unit_price: Price


class POIn(Strict):
    supplier_id: uuid.UUID
    expected_at: datetime | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    lines: Annotated[list[POLineSchema], Field(min_length=1, max_length=500)]


class POLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: uuid.UUID
    quantity: Decimal
    unit: str
    unit_price: Decimal
    quantity_base: Decimal
    received_quantity_base: Decimal


class POOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    supplier_id: uuid.UUID
    status: str
    total: Decimal
    expected_at: datetime | None
    note: str | None
    created_by: uuid.UUID
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    closed_reason: str | None
    lines: list[POLineOut] = []


class POReceiveLine(Strict):
    item_id: uuid.UUID
    quantity: Qty
    unit: UnitCode
    unit_price: Price | None = None


class POReceiveIn(Strict):
    occurred_at: datetime | None = None
    lines: Annotated[list[POReceiveLine], Field(min_length=1, max_length=500)]


class InvoiceIn(Strict):
    invoice_no: Annotated[str, Field(min_length=1, max_length=64)]
    amount: Annotated[Decimal, Field(ge=0, le=Decimal("10000000000"), decimal_places=2)]


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    invoice_no: str
    amount: Decimal
    received_value: Decimal
    difference: Decimal
    status: str


class ChecklistItemSchema(Strict):
    label: Annotated[str, Field(min_length=1, max_length=300)]
    answer_type: Annotated[str, Field(pattern="^(check|number)$")]
    unit: Annotated[str, Field(max_length=16)] | None = None
    min_value: Annotated[Decimal, Field(ge=-1000000, le=1000000, decimal_places=3)] | None = None
    max_value: Annotated[Decimal, Field(ge=-1000000, le=1000000, decimal_places=3)] | None = None
    evidence_required: bool = False


class ChecklistTemplateIn(Strict):
    name: Name
    kind: Annotated[str, Field(max_length=16)]
    frequency: Annotated[str, Field(pattern="^(daily|adhoc)$")] = "daily"
    items: Annotated[list[ChecklistItemSchema], Field(min_length=1, max_length=200)]


class ChecklistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    label: str
    answer_type: str
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    evidence_required: bool


class ChecklistTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    frequency: str
    is_active: bool
    items: list[ChecklistItemOut] = []


class ActivePatch(Strict):
    is_active: bool


class AnswerSchema(Strict):
    item_id: int
    checked: bool | None = None
    value: Annotated[Decimal, Field(ge=-1000000, le=1000000, decimal_places=3)] | None = None
    evidence_ref: Annotated[str, Field(max_length=500)] | None = None
    note: Annotated[str, Field(max_length=500)] | None = None


class ChecklistRunIn(Strict):
    template_id: uuid.UUID
    performed_at: datetime | None = None
    corrects_run_id: uuid.UUID | None = None
    correction_reason: Annotated[str, Field(max_length=500)] | None = None
    answers: Annotated[list[AnswerSchema], Field(min_length=1, max_length=200)]


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: int
    checked: bool | None
    value: Decimal | None
    passed: bool
    evidence_ref: str | None
    note: str | None


class ChecklistRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    template_id: uuid.UUID
    performed_by: uuid.UUID
    performed_at: datetime
    passed: bool
    corrects_run_id: uuid.UUID | None
    correction_reason: str | None
    answers: list[AnswerOut] = []


class NonconformityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    description: str
    responsible_user_id: uuid.UUID | None
    due_at: datetime | None
    status: str
    corrective_action: str | None
    resolved_at: datetime | None
    created_at: datetime


class NcAssignIn(Strict):
    responsible_user_id: uuid.UUID
    due_at: datetime


class NcResolveIn(Strict):
    corrective_action: Annotated[str, Field(min_length=1, max_length=1000)]


class ShiftIn(Strict):
    user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    note: Annotated[str, Field(max_length=300)] | None = None


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    note: str | None
    cancelled_at: datetime | None


class AttendanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    user_id: uuid.UUID
    clock_in_at: datetime
    clock_out_at: datetime | None


class AttendanceCorrectionIn(Strict):
    clock_in_at: datetime
    clock_out_at: datetime | None = None
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class TaskIn(Strict):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    due_at: datetime | None = None
    assigned_to: uuid.UUID | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    title: str
    due_at: datetime | None
    assigned_to: uuid.UUID | None
    status: str
    completed_by: uuid.UUID | None
    completed_at: datetime | None


class HandoverIn(Strict):
    note: Annotated[str, Field(min_length=1, max_length=2000)]


class HandoverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    author_id: uuid.UUID
    note: str
    created_at: datetime


class IntegrationIn(Strict):
    name: Name
    branch_id: uuid.UUID


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    kind: str
    secret_version: int
    is_active: bool


class IntegrationSecretOut(BaseModel):
    integration: IntegrationOut
    # Shown on create and rotate only; it can be rotated, never read back.
    signing_secret: str


class MappingsIn(Strict):
    mappings: Annotated[
        dict[Annotated[str, Field(min_length=1, max_length=64)], uuid.UUID],
        Field(min_length=1, max_length=1000),
    ]


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    external_code: str
    product_id: uuid.UUID


class PosLine(Strict):
    code: Annotated[str, Field(min_length=1, max_length=64)]
    quantity: Annotated[int, Field(gt=0, le=10000)]
    discount: Annotated[Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2)] = Decimal(
        "0"
    )


class PosPayload(Strict):
    event: Annotated[str, Field(pattern="^order\\.(created|cancelled)$")]
    external_id: Annotated[str, Field(min_length=1, max_length=128)]
    ordered_at: datetime | None = None
    reason: Annotated[str, Field(max_length=500)] | None = None
    lines: Annotated[list[PosLine], Field(max_length=200)] = []


class PosEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    external_id: str
    event: str
    status: str
    error: str | None
    order_id: uuid.UUID | None
    attempts: int
    received_at: datetime
    processed_at: datetime | None
