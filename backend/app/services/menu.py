"""Menu products and their recipes. Monthly sales × recipe = the "Satıştan (reçete)" usage of
each count item, e.g. 120 Klasik × 20 GR + 80 Smash × 15 GR = 3.6 KG YOUKİ SOS."""

import json
import uuid
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
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

DEFAULT_MENU = Path(__file__).resolve().parent.parent / "data" / "taffburger_menu.json"


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
        cost = (
            None
            if per is None or item.unit_price is None
            else (per * item.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        )
        lines.setdefault(line.product_id, []).append(
            {
                "item_id": item.id,
                "cost": cost,
                "item_name": item.name,
                "item_unit": item.unit,
                "quantity": line.quantity,
                "unit": line.unit,
                # None: not deducted until the item's package content is entered
                "per_item_unit": None if per is None else per.quantize(Decimal("0.000001")),
            }
        )
    out = []
    for p in products:
        recipe = lines.get(p.id, [])
        known = [ln["cost"] for ln in recipe if ln["cost"] is not None]
        cost = sum(known, Decimal("0")) if recipe else None
        complete = bool(recipe) and len(known) == len(recipe)
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "is_active": p.is_active,
                "sort_order": p.sort_order,
                "price": p.price,
                "recipe": recipe,
                # cost of the priced lines; cost_complete is False when a line lacks a price
                # or a unit conversion, so the ratio is a lower bound then.
                "cost": cost,
                "cost_complete": complete,
                "food_cost_pct": (
                    (cost * 100 / p.price).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
                    if cost is not None and p.price
                    else None
                ),
            }
        )
    return out


def create_product(
    session: Session, actor: Actor, name: str, price: Decimal | None = None
) -> MenuProduct:
    actor.require(Perm.ITEM_MANAGE)
    last = session.scalar(
        select(func.max(MenuProduct.sort_order)).where(MenuProduct.tenant_id == actor.tenant_id)
    )
    p = MenuProduct(
        tenant_id=actor.tenant_id, name=name.strip(), sort_order=(last or 0) + 10, price=price
    )
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
    """Replaces the recipe. A line whose unit cannot be converted yet (2 ADET cheddar for an item
    counted in KG, before its package content is entered) is kept but not deducted; the count
    sheet warns about it."""
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
        new.append(RecipeLine(product_id=p.id, item_id=item.id, quantity=qty, unit=unit))
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


def unconverted(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """Recipe lines that are not deducted because their unit does not convert yet."""
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for line, item, product in session.execute(
        select(RecipeLine, CountItem, MenuProduct)
        .join(CountItem, CountItem.id == RecipeLine.item_id)
        .join(MenuProduct, MenuProduct.id == RecipeLine.product_id)
        .where(MenuProduct.tenant_id == tenant_id)
        .order_by(CountItem.sort_order, MenuProduct.sort_order)
    ):
        if _per_unit(line, item) is None:
            w = out.setdefault(
                item.id,
                {
                    "item_id": item.id,
                    "item_name": item.name,
                    "item_unit": item.unit,
                    "recipe_units": [],
                    "products": [],
                },
            )
            if line.unit not in w["recipe_units"]:
                w["recipe_units"].append(line.unit)
            w["products"].append(product.name)
    return list(out.values())


def load_default_menu(session: Session, actor: Actor, add_missing: bool) -> dict[str, Any]:
    """Loads the burgers from the restaurant's menu boards. Existing products keep what the
    user entered; only recipe lines for items not yet in their recipe are added, so running it
    again after adding the missing ingredients completes the recipes."""
    actor.require(Perm.ITEM_MANAGE)
    data = json.loads(DEFAULT_MENU.read_text(encoding="utf-8"))
    items = {
        i.name: i
        for i in session.scalars(select(CountItem).where(CountItem.tenant_id == actor.tenant_id))
    }
    added_items = []
    if add_missing:
        last = max((i.sort_order for i in items.values()), default=0)
        for extra in data["extra_items"]:
            if extra["name"] in items:
                continue
            last += 10
            item = CountItem(
                tenant_id=actor.tenant_id,
                group_name=extra["group"],
                name=extra["name"],
                unit=extra["unit"],
                sort_order=last,
            )
            session.add(item)
            items[item.name] = item
            added_items.append(item.name)
        session.flush()
    products = {
        p.name: p
        for p in session.scalars(
            select(MenuProduct).where(MenuProduct.tenant_id == actor.tenant_id)
        )
    }
    last_p = max((p.sort_order for p in products.values()), default=0)
    added_products, added_lines = [], 0
    missing: dict[str, list[str]] = {}
    for spec in data["products"]:
        product = products.get(spec["name"])
        if product is None:
            last_p += 10
            product = MenuProduct(
                tenant_id=actor.tenant_id,
                name=spec["name"],
                sort_order=last_p,
                price=Decimal(spec["price"]) if spec.get("price") else None,
            )
            session.add(product)
            session.flush()
            added_products.append(product.name)
        elif product.price is None and spec.get("price"):
            product.price = Decimal(spec["price"])
        have = set(
            session.scalars(select(RecipeLine.item_id).where(RecipeLine.product_id == product.id))
        )
        for name, qty, unit in spec["lines"]:
            found = items.get(name)
            if found is None:
                missing.setdefault(name, []).append(spec["name"])
                continue
            if found.id in have:
                continue
            session.add(
                RecipeLine(
                    product_id=product.id, item_id=found.id, quantity=Decimal(qty), unit=unit
                )
            )
            added_lines += 1
    session.flush()
    audit.record(
        session,
        actor,
        "menu_product.load_defaults",
        "menu_product",
        None,
        {"added": len(added_products), "lines": added_lines, "items": len(added_items)},
    )
    session.commit()
    return {
        "added_products": added_products,
        "added_lines": added_lines,
        "added_items": added_items,
        "missing_items": [{"name": k, "products": v} for k, v in missing.items()],
        "unconverted": unconverted(session, actor.tenant_id),
        "notes": data["notes"],
    }


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


DEFAULT_SUPPLIER = Path(__file__).resolve().parent.parent / "data" / "taffburger_supplier.json"


def apply_supplier_data(session: Session, actor: Actor, overwrite: bool) -> dict[str, Any]:
    """Unit prices and package contents from the supplier price list. Without `overwrite`
    only empty fields are filled, so prices the user entered are kept."""
    actor.require(Perm.ITEM_MANAGE)
    data = json.loads(DEFAULT_SUPPLIER.read_text(encoding="utf-8"))
    items = {
        i.name: i
        for i in session.scalars(select(CountItem).where(CountItem.tenant_id == actor.tenant_id))
    }
    applied: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    not_in_list: list[str] = []
    for row in data["items"]:
        item = items.get(row["item"])
        if item is None:
            not_in_list.append(row["item"])
            continue
        changes: dict[str, str] = {}
        price = Decimal(row["unit_price"])
        if item.unit_price != price and (item.unit_price is None or overwrite):
            item.unit_price = price
            changes["unit_price"] = row["unit_price"]
        if row.get("content_qty"):
            qty, unit = Decimal(row["content_qty"]), row["content_unit"]
            if (item.content_qty, item.content_unit) != (qty, unit) and (
                item.content_qty is None or overwrite
            ):
                item.content_qty, item.content_unit = qty, unit
                changes["content"] = f"1 {item.unit} = {row['content_qty']} {unit}"
        entry = {
            "item": item.name,
            "supplier": row["supplier"],
            "confidence": row["confidence"],
            "note": row.get("note"),
            "changes": changes,
        }
        (applied if changes else kept).append(entry)
    audit.record(
        session,
        actor,
        "count_item.supplier_data",
        "count_item",
        None,
        {"applied": len(applied), "overwrite": overwrite},
    )
    session.commit()
    return {
        "applied": applied,
        "unchanged": kept,
        "not_in_list": not_in_list,
        "not_applied": data["not_applied"],
        "not_in_count_list": data["not_in_count_list"],
        "source": data["source"],
    }
