import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.costing import gross_quantity, money
from app.domain.permissions import Perm
from app.domain.units import BASE_UNITS, STANDARD_UNITS
from app.errors import Conflict, Invalid
from app.models import (
    Item,
    ItemUnitConversion,
    Product,
    RecipeLine,
    RecipeVersion,
    StockBalance,
    StockMovement,
)
from app.security import Actor
from app.services import audit
from app.services.inventory import check_not_future, convert, now
from app.services.scope import get_branch, get_item, get_product


def create_item(
    session: Session,
    actor: Actor,
    *,
    sku: str,
    name: str,
    base_unit: str,
    min_stock: Decimal | None,
    allergens: list[str],
    conversions: dict[str, Decimal],
) -> Item:
    actor.require(Perm.CATALOG_MANAGE)
    if base_unit not in BASE_UNITS:
        raise Invalid(f"base_unit must be one of {sorted(BASE_UNITS)}")
    for unit, factor in conversions.items():
        if factor <= 0:
            raise Invalid(f"conversion factor for '{unit}' must be positive")
        if unit in STANDARD_UNITS:
            raise Invalid(f"'{unit}' is a standard unit and cannot be redefined")
    item = Item(
        tenant_id=actor.tenant_id,
        sku=sku,
        name=name,
        base_unit=base_unit,
        min_stock=min_stock,
        allergens=sorted(set(allergens)),
    )
    session.add(item)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"item sku '{sku}' already exists") from exc
    for unit, factor in conversions.items():
        session.add(ItemUnitConversion(item_id=item.id, unit=unit, factor_to_base=factor))
    audit.record(session, actor, "catalog.item.create", "item", item.id, {"sku": sku})
    session.commit()
    return item


def create_product(
    session: Session, actor: Actor, *, sku: str, name: str, price: Decimal
) -> Product:
    actor.require(Perm.CATALOG_MANAGE)
    if price < 0:
        raise Invalid("price cannot be negative")
    product = Product(tenant_id=actor.tenant_id, sku=sku, name=name, price=price)
    session.add(product)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"product sku '{sku}' already exists") from exc
    audit.record(
        session,
        actor,
        "catalog.product.create",
        "product",
        product.id,
        {"sku": sku, "price": str(price)},
    )
    session.commit()
    return product


@dataclass(frozen=True)
class RecipeLineIn:
    item_id: uuid.UUID
    quantity: Decimal
    unit: str
    loss_pct: Decimal = Decimal("0")


def create_recipe_version(
    session: Session,
    actor: Actor,
    product_id: uuid.UUID,
    lines: list[RecipeLineIn],
    valid_from: datetime | None,
) -> RecipeVersion:
    """Add a new recipe version and close the currently open one.

    Back-dating is rejected: sales already recorded keep the recipe version they were costed
    with, so a back-dated version would make the history ambiguous.
    """
    actor.require(Perm.RECIPE_MANAGE)
    if not lines:
        raise Invalid("recipe has no lines")
    if len({line.item_id for line in lines}) != len(lines):
        raise Invalid("an item appears more than once in the recipe")
    start = check_not_future(valid_from) if valid_from else now()
    if start < now() - timedelta(minutes=5):
        raise Invalid("valid_from cannot be in the past")
    # Lock the product row so concurrent version creation is serialised.
    product = get_product(session, actor, product_id)
    session.execute(select(Product.id).where(Product.id == product.id).with_for_update())
    current = session.scalar(
        select(RecipeVersion).where(
            RecipeVersion.product_id == product.id, RecipeVersion.valid_to.is_(None)
        )
    )
    if current is not None:
        if start <= current.valid_from:
            raise Invalid("valid_from must be after the current version's valid_from")
        current.valid_to = start
    next_version = (
        session.scalar(
            select(func.max(RecipeVersion.version)).where(RecipeVersion.product_id == product.id)
        )
        or 0
    ) + 1
    version = RecipeVersion(
        tenant_id=actor.tenant_id,
        product_id=product.id,
        version=next_version,
        valid_from=start,
        created_by=actor.user_id,
    )
    session.add(version)
    session.flush()
    for line in lines:
        if line.quantity <= 0:
            raise Invalid("recipe quantity must be positive")
        item = get_item(session, actor, line.item_id)
        net_base = convert(session, item, line.quantity, line.unit)
        try:
            gross = gross_quantity(net_base, line.loss_pct)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc
        session.add(
            RecipeLine(
                recipe_version_id=version.id,
                item_id=item.id,
                quantity=line.quantity,
                unit=line.unit,
                loss_pct=line.loss_pct,
                gross_quantity_base=gross,
            )
        )
    audit.record(
        session,
        actor,
        "recipe.version.create",
        "recipe_version",
        version.id,
        {"product_id": str(product.id), "version": next_version, "lines": len(lines)},
    )
    session.commit()
    return version


def recipe_at(session: Session, product_id: uuid.UUID, at: datetime) -> RecipeVersion | None:
    return session.scalar(
        select(RecipeVersion).where(
            RecipeVersion.product_id == product_id,
            RecipeVersion.valid_from <= at,
            (RecipeVersion.valid_to.is_(None)) | (RecipeVersion.valid_to > at),
        )
    )


_UNSET: object = object()


def update_item(
    session: Session,
    actor: Actor,
    item_id: uuid.UUID,
    *,
    name: str | None,
    min_stock: Decimal | None | object = _UNSET,
    allergens: list[str] | None,
    is_active: bool | None,
) -> Item:
    """Base unit and SKU are immutable: every ledger row is stored in the base unit."""
    actor.require(Perm.CATALOG_MANAGE)
    item = get_item(session, actor, item_id, active=False)
    before = {"name": item.name, "min_stock": str(item.min_stock), "is_active": item.is_active}
    if name is not None:
        item.name = name
    if min_stock is not _UNSET:
        if isinstance(min_stock, Decimal) and min_stock < 0:
            raise Invalid("min_stock cannot be negative")
        item.min_stock = min_stock  # type: ignore[assignment]
    if allergens is not None:
        item.allergens = sorted(set(allergens))
    if is_active is not None:
        item.is_active = is_active
    audit.record(
        session,
        actor,
        "catalog.item.update",
        "item",
        item.id,
        {
            "before": before,
            "after": {
                "name": item.name,
                "min_stock": str(item.min_stock),
                "is_active": item.is_active,
            },
        },
    )
    session.commit()
    return item


def update_product(
    session: Session,
    actor: Actor,
    product_id: uuid.UUID,
    *,
    name: str | None,
    price: Decimal | None,
    is_active: bool | None,
) -> Product:
    """Price changes apply to new orders only; order lines keep the price they were sold at."""
    actor.require(Perm.CATALOG_MANAGE)
    product = get_product(session, actor, product_id, active=False)
    before = {"name": product.name, "price": str(product.price), "is_active": product.is_active}
    if name is not None:
        product.name = name
    if price is not None:
        if price < 0:
            raise Invalid("price cannot be negative")
        product.price = price
    if is_active is not None:
        product.is_active = is_active
    audit.record(
        session,
        actor,
        "catalog.product.update",
        "product",
        product.id,
        {
            "before": before,
            "after": {
                "name": product.name,
                "price": str(product.price),
                "is_active": product.is_active,
            },
        },
    )
    session.commit()
    return product


def recipe_cost(
    session: Session, actor: Actor, product_id: uuid.UUID, branch_id: uuid.UUID
) -> dict[str, object]:
    """Theoretical cost of one unit at the branch's current average costs. A line whose item
    has never been received in the branch has unknown cost, so the total is unknown too."""
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    product = get_product(session, actor, product_id, active=False)
    recipe = recipe_at(session, product.id, now())
    if recipe is None:
        return {
            "product_id": product.id,
            "recipe_version_id": None,
            "lines": [],
            "unit_cost": None,
            "status": "no_recipe",
            "price": product.price,
        }
    lines = []
    total: Decimal | None = Decimal("0")
    for rl in session.scalars(select(RecipeLine).where(RecipeLine.recipe_version_id == recipe.id)):
        bal = session.get(StockBalance, (branch_id, rl.item_id))
        # Known only if stock ever came in with a cost (a genuinely free item is still known).
        inbound = session.scalar(
            select(func.count())
            .select_from(StockMovement)
            .where(
                StockMovement.branch_id == branch_id,
                StockMovement.item_id == rl.item_id,
                StockMovement.movement_type.in_(("receipt", "transfer_in")),
            )
        )
        known = bal is not None and (inbound or 0) > 0
        cost = money(rl.gross_quantity_base * bal.avg_cost) if known and bal else None
        if cost is None:
            total = None
        elif total is not None:
            total += cost
        lines.append(
            {
                "item_id": rl.item_id,
                "gross_quantity_base": rl.gross_quantity_base,
                "avg_cost": bal.avg_cost if known and bal else None,
                "cost": cost,
            }
        )
    return {
        "product_id": product.id,
        "recipe_version_id": recipe.id,
        "version": recipe.version,
        "price": product.price,
        "lines": lines,
        "unit_cost": money(total) if total is not None else None,
        "food_cost_pct": (
            (total / product.price * 100).quantize(Decimal("0.01"))
            if total is not None and product.price > 0
            else None
        ),
        "status": "ok" if total is not None else "incomplete_cost",
    }
