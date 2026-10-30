import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.api import schemas as s
from app.api.deps import ActorDep, SessionDep
from app.config import load_settings
from app.domain.permissions import ROLE_LABELS, ROLE_PERMISSIONS
from app.models import User
from app.security import issue_token
from app.services import admin, menu, monthly, monthly_export, monthly_reports
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


@router.get("/me")
def me(actor: ActorDep, session: SessionDep) -> Any:
    """Who am I and what may I do. The UI uses it to hide actions; the API enforces them."""
    user = session.get(User, actor.user_id)
    return {
        "id": actor.user_id,
        "email": user.email if user else None,
        "role": actor.role.value,
        "branch_ids": sorted(str(b) for b in actor.branch_ids)
        if actor.branch_ids is not None
        else None,
        "role_label": ROLE_LABELS[actor.role],
        "permissions": sorted(p.value for p in ROLE_PERMISSIONS[actor.role]),
        "roles": [{"value": r.value, "label": label} for r, label in ROLE_LABELS.items()],
    }


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


@router.get("/users", response_model=list[s.UserListOut])
def list_users(actor: ActorDep, session: SessionDep) -> Any:
    return admin.list_users(session, actor)


@router.post("/users/{user_id}/password", status_code=204)
def reset_user_password(
    user_id: uuid.UUID, body: s.PasswordResetIn, actor: ActorDep, session: SessionDep
) -> None:
    admin.reset_password(session, actor, user_id, body.new_password)


@router.get("/audit-events", response_model=list[s.AuditOut])
def list_audit(
    actor: ActorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    action: Annotated[str | None, Query(max_length=64)] = None,
) -> Any:
    return [
        s.AuditOut.model_validate(e).model_copy(update={"user_email": email})
        for e, email in admin.list_audit(session, actor, limit, offset, action)
    ]


# --- monthly count --------------------------------------------------------------------------


@router.get("/count-items", response_model=list[s.CountItemOut])
def list_count_items(actor: ActorDep, session: SessionDep, include_inactive: bool = False) -> Any:
    return monthly.list_items(session, actor, include_inactive)


@router.post("/count-items", response_model=s.CountItemOut, status_code=201)
def create_count_item(body: s.CountItemIn, actor: ActorDep, session: SessionDep) -> Any:
    return monthly.create_item(
        session,
        actor,
        group_name=body.group_name,
        name=body.name,
        unit=body.unit,
        unit_price=body.unit_price,
        content_qty=body.content_qty,
        content_unit=body.content_unit,
    )


@router.get("/menu-products")
def list_menu_products(actor: ActorDep, session: SessionDep) -> Any:
    """Menu products with their recipes."""
    return menu.list_products(session, actor)


@router.post("/menu-products/load-defaults")
def load_default_menu(actor: ActorDep, session: SessionDep, add_missing_items: bool = False) -> Any:
    """Taff Burger menu boards; add_missing_items also adds ingredients missing from the list."""
    return menu.load_default_menu(session, actor, add_missing_items)


@router.post("/menu-products", status_code=201)
def create_menu_product(body: s.MenuProductIn, actor: ActorDep, session: SessionDep) -> Any:
    p = menu.create_product(session, actor, body.name)
    return {"id": p.id, "name": p.name, "is_active": p.is_active, "sort_order": p.sort_order}


@router.patch("/menu-products/{product_id}")
def update_menu_product(
    product_id: uuid.UUID, body: s.MenuProductPatch, actor: ActorDep, session: SessionDep
) -> Any:
    p = menu.update_product(session, actor, product_id, body.model_dump(exclude_unset=True))
    return {"id": p.id, "name": p.name, "is_active": p.is_active, "sort_order": p.sort_order}


@router.put("/menu-products/{product_id}/recipe")
def set_recipe(
    product_id: uuid.UUID, body: s.RecipeIn, actor: ActorDep, session: SessionDep
) -> Any:
    menu.set_recipe(
        session, actor, product_id, [(x.item_id, x.quantity, x.unit) for x in body.lines]
    )
    return next(p for p in menu.list_products(session, actor) if p["id"] == product_id)


@router.patch("/count-items/{item_id}", response_model=s.CountItemOut)
def update_count_item(
    item_id: uuid.UUID, body: s.CountItemPatch, actor: ActorDep, session: SessionDep
) -> Any:
    return monthly.update_item(session, actor, item_id, body.model_dump(exclude_unset=True))


@router.post("/count-items/load-defaults")
def load_default_count_items(actor: ActorDep, session: SessionDep) -> Any:
    return monthly.load_default_items(session, actor)


@router.get("/branches/{branch_id}/monthly-counts")
def list_monthly_counts(branch_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Any:
    """Every month of the branch, newest first, with its totals."""
    return monthly_reports.month_summaries(session, actor, branch_id)


@router.get("/branches/{branch_id}/dashboard")
def dashboard(
    branch_id: uuid.UUID, period: s.PeriodQuery, actor: ActorDep, session: SessionDep
) -> Any:
    return monthly_reports.dashboard(session, actor, branch_id, period)


@router.get("/branches/{branch_id}/count-items/{item_id}/history")
def item_history(
    branch_id: uuid.UUID, item_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> Any:
    return monthly_reports.item_history(session, actor, branch_id, item_id)


@router.get("/monthly-counts/overview")
def overview(period: s.PeriodQuery, actor: ActorDep, session: SessionDep) -> Any:
    return monthly_reports.overview(session, actor, period)


@router.get("/branches/{branch_id}/monthly-counts/{period}")
def get_monthly_count(
    branch_id: uuid.UUID, period: str, actor: ActorDep, session: SessionDep
) -> Any:
    count = monthly.open_month(session, actor, branch_id, period)
    return monthly.sheet(session, actor, count)


@router.put("/branches/{branch_id}/monthly-counts/{period}")
def save_monthly_count(
    branch_id: uuid.UUID,
    period: str,
    body: s.MonthlySaveIn,
    actor: ActorDep,
    session: SessionDep,
) -> Any:
    monthly.open_month(session, actor, branch_id, period)
    count = monthly.save_lines(
        session,
        actor,
        branch_id,
        period,
        [monthly.LineUpdate(**x.model_dump()) for x in body.lines],
        body.model_dump(exclude_unset=True, exclude={"lines", "sales"}),
        [(x.product_id, x.quantity) for x in body.sales],
    )
    return monthly.sheet(session, actor, count)


@router.post("/branches/{branch_id}/monthly-counts/{period}/close")
def close_monthly_count(
    branch_id: uuid.UUID, period: str, actor: ActorDep, session: SessionDep
) -> Any:
    return monthly.sheet(session, actor, monthly.close_month(session, actor, branch_id, period))


@router.post("/branches/{branch_id}/monthly-counts/{period}/reopen")
def reopen_monthly_count(
    branch_id: uuid.UUID, period: str, body: s.CancelIn, actor: ActorDep, session: SessionDep
) -> Any:
    count = monthly.reopen_month(session, actor, branch_id, period, body.reason)
    return monthly.sheet(session, actor, count)


@router.get("/branches/{branch_id}/monthly-counts/{period}/export.xlsx")
def export_monthly_count(
    branch_id: uuid.UUID, period: str, actor: ActorDep, session: SessionDep
) -> Response:
    branch = get_branch(session, actor, branch_id)
    count = monthly.open_month(session, actor, branch_id, period)
    data = monthly_export.build_workbook(monthly.sheet(session, actor, count), branch.name)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="sayim-{period}.xlsx"'},
    )
