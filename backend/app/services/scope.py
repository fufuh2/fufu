"""Tenant and branch scoping. Every lookup filters by the actor's tenant; cross-tenant ids
resolve to NotFound so callers cannot probe for the existence of other tenants' records."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFound
from app.models import Branch
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
