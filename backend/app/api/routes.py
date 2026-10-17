import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy import select

from app.api import schemas as s
from app.api.deps import ActorDep, SessionDep
from app.config import load_settings
from app.domain.permissions import Perm
from app.models import Item, Product, StockMovement
from app.security import issue_token
from app.services import (
    admin,
    catalog,
    inventory,
    orders,
    purchasing,
    quality,
    reports,
    transfers,
    workforce,
)
from app.services.scope import get_branch

router = APIRouter(prefix="/api/v1")


@router.post("/auth/login", response_model=s.TokenOut)
def login(body: s.LoginIn, session: SessionDep) -> s.TokenOut:
    user = admin.authenticate(session, body.email, body.password)
    settings = load_settings()
    return s.TokenOut(
        access_token=issue_token(
            user.id, user.token_version, settings.jwt_secret, settings.jwt_ttl_minutes
        )
    )


@router.post("/auth/password", status_code=204)
def change_password(body: s.PasswordChangeIn, actor: ActorDep, session: SessionDep) -> None:
    admin.change_password(session, actor, body.current_password, body.new_password)


@router.post("/auth/logout-all", status_code=204)
def logout_all(actor: ActorDep, session: SessionDep) -> None:
    admin.logout_all(session, actor)


# --- administration -------------------------------------------------------------------------


@router.post("/branches", response_model=s.BranchOut, status_code=201)
def create_branch(body: s.BranchIn, actor: ActorDep, session: SessionDep) -> Any:
    return admin.create_branch(session, actor, body.name)


@router.get("/branches", response_model=list[s.BranchOut])
def list_branches(actor: ActorDep, session: SessionDep) -> Any:
    return admin.list_branches(session, actor)


@router.post("/users", response_model=s.UserOut, status_code=201)
def create_user(body: s.UserIn, actor: ActorDep, session: SessionDep) -> Any:
    return admin.create_user(
        session,
        actor,
        email=body.email,
        password=body.password,
        role=body.role,
        branch_ids=body.branch_ids,
    )


@router.patch("/branches/{branch_id}", response_model=s.BranchOut)
def update_branch(
    branch_id: uuid.UUID, body: s.BranchPatch, actor: ActorDep, session: SessionDep
) -> Any:
    return admin.update_branch(session, actor, branch_id, name=body.name, is_active=body.is_active)


@router.patch("/users/{user_id}", response_model=s.UserOut)
def update_user(user_id: uuid.UUID, body: s.UserPatch, actor: ActorDep, session: SessionDep) -> Any:
    return admin.update_user(
        session,
        actor,
        user_id,
        role=body.role,
        branch_ids=body.branch_ids,
        is_active=body.is_active,
    )


@router.get("/audit-events", response_model=list[s.AuditOut])
def list_audit(
    actor: ActorDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> Any:
    return admin.list_audit(session, actor, limit)


# --- catalog --------------------------------------------------------------------------------


@router.post("/items", response_model=s.ItemOut, status_code=201)
def create_item(body: s.ItemIn, actor: ActorDep, session: SessionDep) -> Any:
    return catalog.create_item(
        session,
        actor,
        sku=body.sku,
        name=body.name,
        base_unit=body.base_unit,
        min_stock=body.min_stock,
        target_stock=body.target_stock,
        allergens=body.allergens,
        conversions=body.conversions,
    )


Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/items", response_model=list[s.ItemOut])
def list_items(actor: ActorDep, session: SessionDep, limit: Limit = 200, offset: Offset = 0) -> Any:
    actor.require(Perm.STOCK_READ)
    return session.scalars(
        select(Item)
        .where(Item.tenant_id == actor.tenant_id)
        .order_by(Item.sku)
        .limit(limit)
        .offset(offset)
    )


@router.patch("/items/{item_id}", response_model=s.ItemOut)
def update_item(item_id: uuid.UUID, body: s.ItemPatch, actor: ActorDep, session: SessionDep) -> Any:
    fields = body.model_dump(exclude_unset=True)
    return catalog.update_item(
        session,
        actor,
        item_id,
        name=fields.get("name"),
        min_stock=fields.get("min_stock", catalog._UNSET),
        target_stock=fields.get("target_stock", catalog._UNSET),
        allergens=fields.get("allergens"),
        is_active=fields.get("is_active"),
    )


@router.post("/products", response_model=s.ProductOut, status_code=201)
def create_product(body: s.ProductIn, actor: ActorDep, session: SessionDep) -> Any:
    return catalog.create_product(session, actor, sku=body.sku, name=body.name, price=body.price)


@router.get("/products", response_model=list[s.ProductOut])
def list_products(
    actor: ActorDep, session: SessionDep, limit: Limit = 200, offset: Offset = 0
) -> Any:
    actor.require(Perm.STOCK_READ)
    return session.scalars(
        select(Product)
        .where(Product.tenant_id == actor.tenant_id)
        .order_by(Product.sku)
        .limit(limit)
        .offset(offset)
    )


@router.patch("/products/{product_id}", response_model=s.ProductOut)
def update_product(
    product_id: uuid.UUID, body: s.ProductPatch, actor: ActorDep, session: SessionDep
) -> Any:
    return catalog.update_product(
        session, actor, product_id, name=body.name, price=body.price, is_active=body.is_active
    )


@router.get("/products/{product_id}/cost")
def product_cost(
    product_id: uuid.UUID, branch_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> Any:
    return catalog.recipe_cost(session, actor, product_id, branch_id)


@router.post("/products/{product_id}/recipes", response_model=s.RecipeOut, status_code=201)
def create_recipe(
    product_id: uuid.UUID, body: s.RecipeIn, actor: ActorDep, session: SessionDep
) -> Any:
    return catalog.create_recipe_version(
        session,
        actor,
        product_id,
        [catalog.RecipeLineIn(x.item_id, x.quantity, x.unit, x.loss_pct) for x in body.lines],
        body.valid_from,
    )


# --- inventory ------------------------------------------------------------------------------


@router.post("/branches/{branch_id}/receipts", response_model=list[s.MovementOut], status_code=201)
def receive(branch_id: uuid.UUID, body: s.ReceiptIn, actor: ActorDep, session: SessionDep) -> Any:
    return inventory.receive(
        session,
        actor,
        branch_id,
        [inventory.ReceiptLine(x.item_id, x.quantity, x.unit, x.unit_cost) for x in body.lines],
        body.supplier,
        body.occurred_at,
    )


@router.post("/branches/{branch_id}/waste", response_model=s.MovementOut, status_code=201)
def waste(branch_id: uuid.UUID, body: s.WasteIn, actor: ActorDep, session: SessionDep) -> Any:
    return inventory.record_waste(
        session,
        actor,
        branch_id,
        body.item_id,
        body.quantity,
        body.unit,
        body.reason,
        body.note,
        body.occurred_at,
    )


@router.post("/branches/{branch_id}/counts", response_model=s.CountOut, status_code=201)
def count(branch_id: uuid.UUID, body: s.CountIn, actor: ActorDep, session: SessionDep) -> Any:
    return inventory.post_count(
        session,
        actor,
        branch_id,
        [inventory.CountLine(x.item_id, x.quantity, x.unit) for x in body.lines],
    )


@router.get("/branches/{branch_id}/stock")
def stock(branch_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return reports.stock_on_hand(session, actor, branch_id)


@router.get("/branches/{branch_id}/movements", response_model=list[s.MovementOut])
def movements(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    item_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> Any:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    stmt = (
        select(StockMovement)
        .where(StockMovement.tenant_id == actor.tenant_id, StockMovement.branch_id == branch_id)
        .order_by(StockMovement.id.desc())
        .limit(limit)
    )
    if item_id is not None:
        stmt = stmt.where(StockMovement.item_id == item_id)
    return session.scalars(stmt)


# --- orders ---------------------------------------------------------------------------------


def _order_out(session: SessionDep, order: Any) -> s.OrderOut:
    out = s.OrderOut.model_validate(order)
    out.lines = [s.OrderLineOut.model_validate(x) for x in orders.order_lines(session, order.id)]
    return out


@router.post("/branches/{branch_id}/orders", response_model=s.OrderOut, status_code=201)
def create_order(
    branch_id: uuid.UUID,
    body: s.OrderIn,
    actor: ActorDep,
    session: SessionDep,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> Any:
    order, created = orders.create_order(
        session,
        actor,
        orders.OrderIn(
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            channel=body.channel,
            external_ref=body.external_ref,
            ordered_at=body.ordered_at,
            lines=[orders.OrderLineIn(x.product_id, x.quantity, x.discount) for x in body.lines],
        ),
    )
    if not created:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return _order_out(session, order)


@router.get("/orders/{order_id}", response_model=s.OrderOut)
def get_order(order_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    actor.require(Perm.STOCK_READ)
    return _order_out(session, orders.get_order(session, actor, order_id))


@router.post("/orders/{order_id}/cancel", response_model=s.OrderOut)
def cancel_order(
    order_id: uuid.UUID, body: s.CancelIn, actor: ActorDep, session: SessionDep
) -> Any:
    return _order_out(session, orders.cancel_order(session, actor, order_id, body.reason))


# --- reports --------------------------------------------------------------------------------


@router.get("/branches/{branch_id}/reports/variance")
def variance_report(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
) -> Any:
    return reports.variance(session, actor, branch_id, start, end)


@router.get("/branches/{branch_id}/reports/margin")
def margin_report(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
) -> Any:
    return reports.margin(session, actor, branch_id, start, end)


# --- transfers ------------------------------------------------------------------------------


def _transfer_out(session: SessionDep, transfer: Any) -> s.TransferOut:
    out = s.TransferOut.model_validate(transfer)
    out.lines = [
        s.TransferLineOut.model_validate(x) for x in transfers.transfer_lines(session, transfer.id)
    ]
    return out


@router.post("/branches/{branch_id}/transfers", response_model=s.TransferOut, status_code=201)
def ship_transfer(
    branch_id: uuid.UUID, body: s.TransferIn, actor: ActorDep, session: SessionDep
) -> Any:
    transfer = transfers.ship(
        session,
        actor,
        branch_id,
        body.to_branch_id,
        [transfers.TransferLineIn(x.item_id, x.quantity, x.unit) for x in body.lines],
        body.note,
    )
    return _transfer_out(session, transfer)


@router.get("/branches/{branch_id}/transfers", response_model=list[s.TransferOut])
def list_transfers(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    status: Annotated[str | None, Query(pattern="^(shipped|received|cancelled)$")] = None,
) -> Any:
    return [
        _transfer_out(session, t)
        for t in transfers.list_transfers(session, actor, branch_id, status)
    ]


@router.get("/transfers/{transfer_id}", response_model=s.TransferOut)
def get_transfer(transfer_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    actor.require(Perm.STOCK_READ)
    return _transfer_out(session, transfers.get_transfer(session, actor, transfer_id))


@router.post("/transfers/{transfer_id}/receive", response_model=s.TransferOut)
def receive_transfer(
    transfer_id: uuid.UUID, body: s.TransferReceiveIn, actor: ActorDep, session: SessionDep
) -> Any:
    lines = (
        None
        if body.lines is None
        else [transfers.ReceiveLineIn(x.item_id, x.quantity, x.unit) for x in body.lines]
    )
    return _transfer_out(session, transfers.receive(session, actor, transfer_id, lines))


@router.post("/transfers/{transfer_id}/cancel", response_model=s.TransferOut)
def cancel_transfer(
    transfer_id: uuid.UUID, body: s.CancelIn, actor: ActorDep, session: SessionDep
) -> Any:
    return _transfer_out(session, transfers.cancel(session, actor, transfer_id, body.reason))


# --- purchasing -----------------------------------------------------------------------------


@router.post("/suppliers", response_model=s.SupplierOut, status_code=201)
def create_supplier(body: s.SupplierIn, actor: ActorDep, session: SessionDep) -> Any:
    return purchasing.create_supplier(
        session, actor, name=body.name, tax_id=body.tax_id, email=body.email, phone=body.phone
    )


@router.get("/suppliers", response_model=list[s.SupplierOut])
def list_suppliers(actor: ActorDep, session: SessionDep) -> Any:
    return purchasing.list_suppliers(session, actor)


@router.patch("/suppliers/{supplier_id}", response_model=s.SupplierOut)
def update_supplier(
    supplier_id: uuid.UUID, body: s.SupplierPatch, actor: ActorDep, session: SessionDep
) -> Any:
    return purchasing.update_supplier(
        session, actor, supplier_id, body.model_dump(exclude_unset=True)
    )


@router.post("/suppliers/{supplier_id}/prices", response_model=s.SupplierPriceOut, status_code=201)
def record_quote(
    supplier_id: uuid.UUID, body: s.QuoteIn, actor: ActorDep, session: SessionDep
) -> Any:
    return purchasing.record_quote(
        session, actor, supplier_id, body.item_id, body.unit_price, body.unit
    )


@router.get("/suppliers/{supplier_id}/prices", response_model=list[s.SupplierPriceOut])
def price_history(
    supplier_id: uuid.UUID, item_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> Any:
    return purchasing.price_history(session, actor, supplier_id, item_id)


def _po_out(session: SessionDep, po: Any) -> s.POOut:
    out = s.POOut.model_validate(po)
    out.lines = [s.POLineOut.model_validate(x) for x in purchasing.po_lines(session, po.id)]
    return out


@router.post("/branches/{branch_id}/purchase-orders", response_model=s.POOut, status_code=201)
def create_po(branch_id: uuid.UUID, body: s.POIn, actor: ActorDep, session: SessionDep) -> Any:
    po = purchasing.create_po(
        session,
        actor,
        branch_id,
        body.supplier_id,
        [purchasing.POLineIn(x.item_id, x.quantity, x.unit, x.unit_price) for x in body.lines],
        body.expected_at,
        body.note,
    )
    return _po_out(session, po)


@router.get("/branches/{branch_id}/purchase-orders", response_model=list[s.POOut])
def list_pos(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    status: Annotated[str | None, Query(max_length=24)] = None,
) -> Any:
    return [_po_out(session, p) for p in purchasing.list_pos(session, actor, branch_id, status)]


@router.get("/branches/{branch_id}/purchase-suggestions")
def purchase_suggestions(branch_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return purchasing.suggestions(session, actor, branch_id)


@router.get("/purchase-orders/{po_id}", response_model=s.POOut)
def get_po(po_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    actor.require(Perm.STOCK_READ)
    return _po_out(session, purchasing.get_po(session, actor, po_id))


@router.post("/purchase-orders/{po_id}/submit", response_model=s.POOut)
def submit_po(po_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return _po_out(session, purchasing.submit_po(session, actor, po_id))


@router.post("/purchase-orders/{po_id}/approve", response_model=s.POOut)
def approve_po(po_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return _po_out(session, purchasing.approve_po(session, actor, po_id))


@router.post("/purchase-orders/{po_id}/cancel", response_model=s.POOut)
def cancel_po(po_id: uuid.UUID, body: s.CancelIn, actor: ActorDep, session: SessionDep) -> Any:
    return _po_out(session, purchasing.cancel_po(session, actor, po_id, body.reason))


@router.post("/purchase-orders/{po_id}/close", response_model=s.POOut)
def close_po(po_id: uuid.UUID, body: s.CancelIn, actor: ActorDep, session: SessionDep) -> Any:
    return _po_out(session, purchasing.close_po(session, actor, po_id, body.reason))


@router.post("/purchase-orders/{po_id}/receipts", status_code=201)
def receive_po(po_id: uuid.UUID, body: s.POReceiveIn, actor: ActorDep, session: SessionDep) -> Any:
    return purchasing.receive_po(
        session,
        actor,
        po_id,
        [
            purchasing.POReceiveLineIn(x.item_id, x.quantity, x.unit, x.unit_price)
            for x in body.lines
        ],
        body.occurred_at,
    )


@router.post("/purchase-orders/{po_id}/invoices", response_model=s.InvoiceOut, status_code=201)
def record_invoice(
    po_id: uuid.UUID, body: s.InvoiceIn, actor: ActorDep, session: SessionDep
) -> Any:
    return purchasing.record_invoice(session, actor, po_id, body.invoice_no, body.amount)


From = Annotated[datetime, Query(alias="from")]
To = Annotated[datetime, Query(alias="to")]


# --- quality --------------------------------------------------------------------------------


def _template_out(session: SessionDep, t: Any) -> s.ChecklistTemplateOut:
    out = s.ChecklistTemplateOut.model_validate(t)
    out.items = [
        s.ChecklistItemOut.model_validate(i) for i in quality.template_items(session, t.id)
    ]
    return out


def _run_out(session: SessionDep, r: Any) -> s.ChecklistRunOut:
    out = s.ChecklistRunOut.model_validate(r)
    out.answers = [s.AnswerOut.model_validate(a) for a in quality.run_answers(session, r.id)]
    return out


@router.post("/checklist-templates", response_model=s.ChecklistTemplateOut, status_code=201)
def create_template(body: s.ChecklistTemplateIn, actor: ActorDep, session: SessionDep) -> Any:
    t = quality.create_template(
        session,
        actor,
        name=body.name,
        kind=body.kind,
        frequency=body.frequency,
        items=[quality.TemplateItemIn(**i.model_dump()) for i in body.items],
    )
    return _template_out(session, t)


@router.get("/checklist-templates", response_model=list[s.ChecklistTemplateOut])
def list_templates(actor: ActorDep, session: SessionDep) -> Any:
    return [_template_out(session, t) for t in quality.list_templates(session, actor)]


@router.patch("/checklist-templates/{template_id}", response_model=s.ChecklistTemplateOut)
def update_template(
    template_id: uuid.UUID, body: s.ActivePatch, actor: ActorDep, session: SessionDep
) -> Any:
    return _template_out(
        session, quality.set_template_active(session, actor, template_id, body.is_active)
    )


@router.post(
    "/branches/{branch_id}/checklist-runs", response_model=s.ChecklistRunOut, status_code=201
)
def record_run(
    branch_id: uuid.UUID, body: s.ChecklistRunIn, actor: ActorDep, session: SessionDep
) -> Any:
    run = quality.record_run(
        session,
        actor,
        branch_id,
        body.template_id,
        [quality.AnswerIn(**a.model_dump()) for a in body.answers],
        body.performed_at,
        body.corrects_run_id,
        body.correction_reason,
    )
    return _run_out(session, run)


@router.get("/branches/{branch_id}/checklist-runs", response_model=list[s.ChecklistRunOut])
def list_runs(
    branch_id: uuid.UUID, actor: ActorDep, session: SessionDep, start: From, end: To
) -> Any:
    return [_run_out(session, r) for r in quality.list_runs(session, actor, branch_id, start, end)]


@router.get("/branches/{branch_id}/nonconformities", response_model=list[s.NonconformityOut])
def list_ncs(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    status: Annotated[str | None, Query(pattern="^(open|resolved)$")] = None,
) -> Any:
    return quality.list_nonconformities(session, actor, branch_id, status)


@router.post("/nonconformities/{nc_id}/assign", response_model=s.NonconformityOut)
def assign_nc(nc_id: uuid.UUID, body: s.NcAssignIn, actor: ActorDep, session: SessionDep) -> Any:
    return quality.assign_nonconformity(
        session, actor, nc_id, body.responsible_user_id, body.due_at
    )


@router.post("/nonconformities/{nc_id}/resolve", response_model=s.NonconformityOut)
def resolve_nc(nc_id: uuid.UUID, body: s.NcResolveIn, actor: ActorDep, session: SessionDep) -> Any:
    return quality.resolve_nonconformity(session, actor, nc_id, body.corrective_action)


@router.get("/branches/{branch_id}/reports/quality")
def quality_report(
    branch_id: uuid.UUID, actor: ActorDep, session: SessionDep, start: From, end: To
) -> Any:
    return quality.score(session, actor, branch_id, start, end)


# --- workforce ------------------------------------------------------------------------------


@router.post("/branches/{branch_id}/shifts", response_model=s.ShiftOut, status_code=201)
def create_shift(
    branch_id: uuid.UUID, body: s.ShiftIn, actor: ActorDep, session: SessionDep
) -> Any:
    return workforce.create_shift(
        session, actor, branch_id, body.user_id, body.starts_at, body.ends_at, body.note
    )


@router.get("/branches/{branch_id}/shifts", response_model=list[s.ShiftOut])
def list_shifts(
    branch_id: uuid.UUID, actor: ActorDep, session: SessionDep, start: From, end: To
) -> Any:
    return workforce.list_shifts(session, actor, branch_id, start, end)


@router.post("/shifts/{shift_id}/cancel", response_model=s.ShiftOut)
def cancel_shift(shift_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return workforce.cancel_shift(session, actor, shift_id)


@router.post(
    "/branches/{branch_id}/attendance/clock-in", response_model=s.AttendanceOut, status_code=201
)
def clock_in(branch_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return workforce.clock_in(session, actor, branch_id)


@router.post("/attendance/clock-out", response_model=s.AttendanceOut)
def clock_out(actor: ActorDep, session: SessionDep) -> Any:
    return workforce.clock_out(session, actor)


@router.patch("/attendance/{attendance_id}", response_model=s.AttendanceOut)
def correct_attendance(
    attendance_id: uuid.UUID, body: s.AttendanceCorrectionIn, actor: ActorDep, session: SessionDep
) -> Any:
    return workforce.correct_attendance(
        session, actor, attendance_id, body.clock_in_at, body.clock_out_at, body.reason
    )


@router.get("/branches/{branch_id}/attendance", response_model=list[s.AttendanceOut])
def list_attendance(
    branch_id: uuid.UUID, actor: ActorDep, session: SessionDep, start: From, end: To
) -> Any:
    return workforce.list_attendance(session, actor, branch_id, start, end)


@router.get("/branches/{branch_id}/reports/hours")
def hours_report(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    start: From,
    end: To,
    weekly_limit_hours: Annotated[Decimal, Query(gt=0, le=168)] = Decimal("45"),
    daily_limit_hours: Annotated[Decimal, Query(gt=0, le=24)] = Decimal("11"),
) -> Any:
    return workforce.hours_report(
        session, actor, branch_id, start, end, weekly_limit_hours, daily_limit_hours
    )


@router.post("/branches/{branch_id}/tasks", response_model=s.TaskOut, status_code=201)
def create_task(branch_id: uuid.UUID, body: s.TaskIn, actor: ActorDep, session: SessionDep) -> Any:
    return workforce.create_task(
        session, actor, branch_id, body.title, body.due_at, body.assigned_to
    )


@router.get("/branches/{branch_id}/tasks", response_model=list[s.TaskOut])
def list_tasks(
    branch_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    status: Annotated[str | None, Query(pattern="^(open|done|cancelled)$")] = None,
) -> Any:
    return workforce.list_tasks(session, actor, branch_id, status)


@router.get("/branches/{branch_id}/reports/tasks")
def task_report(branch_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    actor.require(Perm.REPORT_READ)
    return workforce.task_summary(session, actor, branch_id)


@router.post("/tasks/{task_id}/complete", response_model=s.TaskOut)
def complete_task(task_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return workforce.complete_task(session, actor, task_id)


@router.post("/branches/{branch_id}/handovers", response_model=s.HandoverOut, status_code=201)
def add_handover(
    branch_id: uuid.UUID, body: s.HandoverIn, actor: ActorDep, session: SessionDep
) -> Any:
    return workforce.add_handover(session, actor, branch_id, body.note)


@router.get("/branches/{branch_id}/handovers", response_model=list[s.HandoverOut])
def list_handovers(branch_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    return workforce.list_handovers(session, actor, branch_id)
