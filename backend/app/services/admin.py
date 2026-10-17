import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.permissions import TENANT_WIDE_ROLES, Perm, Role
from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import AuditEvent, Branch, Tenant, User, UserBranchAccess
from app.security import Actor, hash_password
from app.services import audit

MIN_PASSWORD_LENGTH = 12


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise Invalid(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def bootstrap_tenant(
    session: Session, *, name: str, owner_email: str, owner_password: str, currency: str = "TRY"
) -> tuple[Tenant, User]:
    """Platform-level operation (CLI only, no HTTP endpoint)."""
    _check_password(owner_password)
    tenant = Tenant(name=name, currency=currency)
    session.add(tenant)
    session.flush()
    owner = User(
        tenant_id=tenant.id,
        email=owner_email.lower(),
        password_hash=hash_password(owner_password),
        role=Role.OWNER.value,
    )
    session.add(owner)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("e-mail already registered") from exc
    session.add(
        AuditEvent(
            tenant_id=tenant.id,
            user_id=None,
            action="tenant.bootstrap",
            entity_type="tenant",
            entity_id=str(tenant.id),
            data={"owner_email": owner.email},
        )
    )
    session.commit()
    return tenant, owner


def load_actor(session: Session, user_id: uuid.UUID) -> Actor | None:
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    role = Role(user.role)
    branch_ids = None
    if role not in TENANT_WIDE_ROLES:
        branch_ids = frozenset(
            session.scalars(
                select(UserBranchAccess.branch_id).where(UserBranchAccess.user_id == user.id)
            )
        )
    return Actor(user_id=user.id, tenant_id=user.tenant_id, role=role, branch_ids=branch_ids)


def create_branch(session: Session, actor: Actor, name: str) -> Branch:
    actor.require(Perm.BRANCH_MANAGE)
    branch = Branch(tenant_id=actor.tenant_id, name=name)
    session.add(branch)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("branch name already exists") from exc
    audit.record(session, actor, "branch.create", "branch", branch.id, {"name": name})
    session.commit()
    return branch


def list_branches(session: Session, actor: Actor) -> list[Branch]:
    stmt = select(Branch).where(Branch.tenant_id == actor.tenant_id).order_by(Branch.name)
    if actor.branch_ids is not None:
        stmt = stmt.where(Branch.id.in_(actor.branch_ids))
    return list(session.scalars(stmt))


def create_user(
    session: Session,
    actor: Actor,
    *,
    email: str,
    password: str,
    role: Role,
    branch_ids: list[uuid.UUID],
) -> User:
    actor.require(Perm.USER_MANAGE)
    if role == Role.OWNER and actor.role != Role.OWNER:
        raise Forbidden("only an owner can create another owner")
    _check_password(password)
    for bid in branch_ids:
        if (
            session.scalar(
                select(Branch.id).where(Branch.id == bid, Branch.tenant_id == actor.tenant_id)
            )
            is None
        ):
            raise NotFound(f"branch {bid} not found")
    if role not in TENANT_WIDE_ROLES and not branch_ids:
        raise Invalid("branch-scoped roles need at least one branch")
    user = User(
        tenant_id=actor.tenant_id,
        email=email.lower(),
        password_hash=hash_password(password),
        role=role.value,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("e-mail already registered") from exc
    for bid in set(branch_ids):
        session.add(UserBranchAccess(user_id=user.id, branch_id=bid))
    audit.record(
        session,
        actor,
        "user.create",
        "user",
        user.id,
        {"role": role.value, "branch_ids": sorted(str(b) for b in set(branch_ids))},
    )
    session.commit()
    return user


def list_audit(session: Session, actor: Actor, limit: int) -> list[AuditEvent]:
    actor.require(Perm.AUDIT_READ)
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.tenant_id == actor.tenant_id)
        .order_by(AuditEvent.id.desc())
        .limit(min(max(limit, 1), 500))
    )
    if actor.branch_ids is not None:
        stmt = stmt.where(
            AuditEvent.data["branch_id"].astext.in_([str(b) for b in actor.branch_ids])
        )
    return list(session.scalars(stmt))
