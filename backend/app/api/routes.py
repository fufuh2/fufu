import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy import select

from app.api import schemas as s
from app.api.deps import ActorDep, SessionDep
from app.config import load_settings
from app.domain.permissions import Perm
from app.errors import DomainError
from app.models import Item, Product, StockMovement, User
from app.security import issue_token, verify_password
from app.services import admin, catalog, inventory, orders, reports
from app.services.scope import get_branch

router = APIRouter(prefix="/api/v1")


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


@router.post("/auth/login", response_model=s.TokenOut)
def login(body: s.LoginIn, session: SessionDep) -> s.TokenOut:
    user = session.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active:
        raise Unauthorized("invalid credentials")
    settings = load_settings()
    return s.TokenOut(
        access_token=issue_token(user.id, settings.jwt_secret, settings.jwt_ttl_minutes)
    )


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
        allergens=body.allergens,
        conversions=body.conversions,
    )


@router.get("/items", response_model=list[s.ItemOut])
def list_items(actor: ActorDep, session: SessionDep) -> Any:
    actor.require(Perm.STOCK_READ)
    return session.scalars(select(Item).where(Item.tenant_id == actor.tenant_id).order_by(Item.sku))


@router.post("/products", response_model=s.ProductOut, status_code=201)
def create_product(body: s.ProductIn, actor: ActorDep, session: SessionDep) -> Any:
    return catalog.create_product(session, actor, sku=body.sku, name=body.name, price=body.price)


@router.get("/products", response_model=list[s.ProductOut])
def list_products(actor: ActorDep, session: SessionDep) -> Any:
    return session.scalars(
        select(Product).where(Product.tenant_id == actor.tenant_id).order_by(Product.sku)
    )


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
