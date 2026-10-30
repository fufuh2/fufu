"""Menu products and their recipes. Monthly sales × recipe = the "Satıştan (reçete)" usage of
each count item, e.g. 120 Klasik × 20 GR + 80 Smash × 15 GR = 3.6 KG YOUKİ SOS."""

import uuid
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.monthly import Q3, to_item_units
from app.domain.permissions import Perm
from app.errors import Conflict, Invalid, NotFound
from app.models import COUNT_UNITS, CountItem, MenuProduct, MonthlySale, RecipeLine
from app.security import Actor
from app.services import audit


def _per_unit(line: RecipeLine, item: CountItem) -> Decimal | None:
    return to_item_units(line.quantity, line.unit, item.unit, item.content_qty, item.content_unit)


def _product(session: Session, actor: Actor, product_id: uuid.UUID) -> MenuProduct:
    p = session.scalar(
        select(MenuProduct).where(
            MenuProduct.id == product_id, MenuProduct.tenant_id == actor.tenant_id
        )
    )
    if p is None:
        raise NotFound("menü ürünü bulunamadı")
    return p


def list_products(session: Session, actor: Actor) -> list[dict[str, Any]]:
    actor.require(Perm.COUNT_READ)
    products = list(
        session.scalars(
            select(MenuProduct)
            .where(MenuProduct.tenant_id == actor.tenant_id)
            .order_by(MenuProduct.sort_order, MenuProduct.name)
        )
    )
    lines: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for line, item in session.execute(
        select(RecipeLine, CountItem)
        .join(CountItem, CountItem.id == RecipeLine.item_id)
        .where(RecipeLine.product_id.in_([p.id for p in products]))
        .order_by(CountItem.sort_order, CountItem.name)
    ):
        per = _per_unit(line, item)
        lines.setdefault(line.product_id, []).append(
            {
                "item_id": item.id,
                "item_name": item.name,
                "item_unit": item.unit,
                "quantity": line.quantity,
                "unit": line.unit,
                "per_item_unit": None if per is None else per.quantize(Decimal("0.000001")),
            }
        )
    return [
        {
            "id": p.id,
            "name": p.name,
            "is_active": p.is_active,
            "sort_order": p.sort_order,
            "recipe": lines.get(p.id, []),
        }
        for p in products
    ]


def create_product(session: Session, actor: Actor, name: str) -> MenuProduct:
    actor.require(Perm.ITEM_MANAGE)
    last = session.scalar(
        select(func.max(MenuProduct.sort_order)).where(MenuProduct.tenant_id == actor.tenant_id)
    )
    p = MenuProduct(tenant_id=actor.tenant_id, name=name.strip(), sort_order=(last or 0) + 10)
    session.add(p)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("bu isimde bir menü ürünü zaten var") from exc
    audit.record(session, actor, "menu_product.create", "menu_product", p.id, {"name": p.name})
    session.commit()
    return p


def update_product(
    session: Session, actor: Actor, product_id: uuid.UUID, fields: dict[str, Any]
) -> MenuProduct:
    actor.require(Perm.ITEM_MANAGE)
    p = _product(session, actor, product_id)
    for key, value in fields.items():
        setattr(p, key, value.strip() if isinstance(value, str) else value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("bu isimde bir menü ürünü zaten var") from exc
    audit.record(
        session,
        actor,
        "menu_product.update",
        "menu_product",
        p.id,
        {"name": p.name, "after": {k: str(v) for k, v in fields.items()}},
    )
    session.commit()
    return p


def set_recipe(
    session: Session,
    actor: Actor,
    product_id: uuid.UUID,
    lines: list[tuple[uuid.UUID, Decimal, str]],
) -> MenuProduct:
    """Replaces the recipe. Every unit must convert into the unit the item is counted in."""
    actor.require(Perm.ITEM_MANAGE)
    p = _product(session, actor, product_id)
    seen: set[uuid.UUID] = set()
    new = []
    for item_id, qty, unit in lines:
        if item_id in seen:
            raise Invalid("bir malzeme reçetede bir kez yer alabilir")
        seen.add(item_id)
        if unit not in COUNT_UNITS:
            raise Invalid(f"birim şunlardan biri olmalı: {', '.join(COUNT_UNITS)}")
        item = session.scalar(
            select(CountItem).where(CountItem.id == item_id, CountItem.tenant_id == actor.tenant_id)
        )
        if item is None:
            raise NotFound("malzeme bulunamadı")
        line = RecipeLine(product_id=p.id, item_id=item.id, quantity=qty, unit=unit)
        if _per_unit(line, item) is None:
            raise Invalid(
                f"{item.name} {item.unit} ile sayılıyor; {unit} çevrilemiyor. "
                f"Ürün Listesi'nde paket içeriğini girin (örn. 1 {item.unit} = 9,5 KG)."
            )
        new.append(line)
    session.execute(delete(RecipeLine).where(RecipeLine.product_id == p.id))
    session.add_all(new)
    audit.record(
        session,
        actor,
        "menu_product.recipe",
        "menu_product",
        p.id,
        {"name": p.name, "lines": len(new)},
    )
    session.commit()
    return p


def check_item_still_convertible(session: Session, item: CountItem) -> None:
    """Called before an item's unit or package content changes."""
    bad = [
        name
        for line, name in session.execute(
            select(RecipeLine, MenuProduct.name)
            .join(MenuProduct, MenuProduct.id == RecipeLine.product_id)
            .where(RecipeLine.item_id == item.id)
        )
        if _per_unit(line, item) is None
    ]
    if bad:
        raise Invalid(
            f"{item.name} şu reçetelerde kullanılıyor ve yeni birime çevrilemiyor: "
            f"{', '.join(sorted(bad))}"
        )


def recipes_by_item(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, list[tuple]]:
    """item_id -> [(product_id, amount per sold product in the item's unit)]."""
    out: dict[uuid.UUID, list[tuple]] = {}
    for line, item in session.execute(
        select(RecipeLine, CountItem)
        .join(CountItem, CountItem.id == RecipeLine.item_id)
        .where(CountItem.tenant_id == tenant_id)
    ):
        per = _per_unit(line, item)
        if per is not None:
            out.setdefault(item.id, []).append((line.product_id, per))
    return out


def sales_of(session: Session, count_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    return {
        s.product_id: s.quantity
        for s in session.scalars(select(MonthlySale).where(MonthlySale.count_id == count_id))
    }


def recipe_usage(session: Session, tenant_id: uuid.UUID, count_id: uuid.UUID) -> dict:
    sales = sales_of(session, count_id)
    usage: dict[uuid.UUID, Decimal] = {}
    for item_id, uses in recipes_by_item(session, tenant_id).items():
        total = sum((sales.get(pid, Decimal("0")) * per for pid, per in uses), Decimal("0"))
        usage[item_id] = total.quantize(Q3, rounding=ROUND_HALF_EVEN)
    return usage
