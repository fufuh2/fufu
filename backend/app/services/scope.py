"""Tenant and branch scoping. Every lookup filters by the actor's tenant; cross-tenant ids
resolve to NotFound so callers cannot probe for the existence of other tenants' records."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFound
from app.models import Branch, Item, Product
from app.security import Actor


def get_branch(session: Session, actor: Actor, branch_id: uuid.UUID) -> Branch:
    branch = session.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.tenant_id == actor.tenant_id)
    )
    if branch is None or (actor.branch_ids is not None and branch_id not in actor.branch_ids):
        raise NotFound("branch not found")
    return branch


def get_active_branch(session: Session, actor: Actor, branch_id: uuid.UUID) -> Branch:
    branch = get_branch(session, actor, branch_id)
    if not branch.is_active:
        raise NotFound("branch is not active")
    return branch


def get_item(session: Session, actor: Actor, item_id: uuid.UUID, *, active: bool = True) -> Item:
    item = session.scalar(select(Item).where(Item.id == item_id, Item.tenant_id == actor.tenant_id))
    if item is None or (active and not item.is_active):
        raise NotFound(f"item {item_id} not found or inactive")
    return item


def get_product(
    session: Session, actor: Actor, product_id: uuid.UUID, *, active: bool = True
) -> Product:
    product = session.scalar(
        select(Product).where(Product.id == product_id, Product.tenant_id == actor.tenant_id)
    )
    if product is None or (active and not product.is_active):
        raise NotFound(f"product {product_id} not found or inactive")
    return product
