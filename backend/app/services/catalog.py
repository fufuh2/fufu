import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.costing import gross_quantity
from app.domain.permissions import Perm
from app.domain.units import BASE_UNITS, STANDARD_UNITS
from app.errors import Conflict, Invalid
from app.models import Item, ItemUnitConversion, Product, RecipeLine, RecipeVersion
from app.security import Actor
from app.services import audit
from app.services.inventory import check_not_future, convert, now
from app.services.scope import get_item, get_product


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
