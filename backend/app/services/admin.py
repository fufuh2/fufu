import uuid
from datetime import timedelta

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.permissions import TENANT_WIDE_ROLES, Perm, Role
from app.errors import Conflict, Forbidden, Invalid, NotFound, TooManyAttempts, Unauthorized
from app.models import AuditEvent, Branch, Tenant, User, UserBranchAccess
from app.security import Actor, hash_password, verify_password
from app.services import audit

MIN_PASSWORD_LENGTH = 12
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=15)


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise Invalid(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def bootstrap_tenant(
    session: Session, *, name: str, owner_email: str, owner_password: str, currency: str = "TRY"
) -> tuple[Tenant, User]:
    """Platform-level operation (CLI only, no HTTP endpoint)."""
    _check_password(owner_password)
    try:
        # Same rule as the login endpoint, otherwise the owner could never sign in.
        owner_email = TypeAdapter(EmailStr).validate_python(owner_email)
    except ValidationError as exc:
        raise Invalid("owner e-mail is not a valid address") from exc
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


def load_actor(
    session: Session, user_id: uuid.UUID, token_version: int | None = None
) -> Actor | None:
    """None for unknown/inactive users and for tokens revoked by a version bump.
    `token_version=None` is for trusted in-process callers (CLI, tests), not HTTP."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    if token_version is not None and token_version != user.token_version:
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


def authenticate(session: Session, email: str, password: str) -> User:
    """Password login with a per-account failure limit.

    Failed attempts are stored in the append-only audit log and also drive the limit, so
    the lockout survives restarts and works across worker processes. Attempts made while
    locked are not recorded, which bounds log growth per account.
    """
    email = email.lower()
    recent_failures = session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.action == "auth.login_failed",
            AuditEvent.data["email"].astext == email,
            AuditEvent.at >= func.now() - LOGIN_WINDOW,
        )
    )
    if (recent_failures or 0) >= LOGIN_MAX_FAILURES:
        raise TooManyAttempts("too many failed attempts; try again later")
    user = session.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        session.add(
            AuditEvent(
                tenant_id=user.tenant_id if user else None,
                user_id=user.id if user else None,
                action="auth.login_failed",
                entity_type="user",
                entity_id=str(user.id) if user else None,
                data={"email": email},
            )
        )
        session.commit()
        raise Unauthorized("invalid credentials")
    session.add(
        AuditEvent(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action="auth.login",
            entity_type="user",
            entity_id=str(user.id),
            data={},
        )
    )
    session.commit()
    return user


def _get_user(session: Session, actor: Actor, user_id: uuid.UUID) -> User:
    user = session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == actor.tenant_id).with_for_update()
    )
    if user is None:
        raise NotFound("user not found")
    return user


def update_user(
    session: Session,
    actor: Actor,
    user_id: uuid.UUID,
    *,
    role: Role | None,
    branch_ids: list[uuid.UUID] | None,
    is_active: bool | None,
) -> User:
    """Role, branch scope and activation. Any change revokes the user's existing tokens."""
    actor.require(Perm.USER_MANAGE)
    user = _get_user(session, actor, user_id)
    if user.id == actor.user_id and (
        (role is not None and role != Role(user.role)) or is_active is False
    ):
        raise Invalid("you cannot change your own role or deactivate yourself")
    if role == Role.OWNER and actor.role != Role.OWNER:
        raise Forbidden("only an owner can grant the owner role")
    before = {"role": user.role, "is_active": user.is_active}
    if role is not None:
        user.role = role.value
    if is_active is not None:
        user.is_active = is_active
    if branch_ids is not None:
        for bid in branch_ids:
            if (
                session.scalar(
                    select(Branch.id).where(Branch.id == bid, Branch.tenant_id == actor.tenant_id)
                )
                is None
            ):
                raise NotFound(f"branch {bid} not found")
        session.execute(delete(UserBranchAccess).where(UserBranchAccess.user_id == user.id))
        for bid in set(branch_ids):
            session.add(UserBranchAccess(user_id=user.id, branch_id=bid))
    if Role(user.role) not in TENANT_WIDE_ROLES:
        has_branch = session.scalar(
            select(func.count())
            .select_from(UserBranchAccess)
            .where(UserBranchAccess.user_id == user.id)
        )
        if not has_branch:
            raise Invalid("branch-scoped roles need at least one branch")
    user.token_version += 1
    audit.record(
        session,
        actor,
        "user.update",
        "user",
        user.id,
        {
            "before": before,
            "after": {"role": user.role, "is_active": user.is_active},
            "branch_ids": sorted(str(b) for b in branch_ids) if branch_ids is not None else None,
        },
    )
    session.commit()
    return user


def change_password(session: Session, actor: Actor, current: str, new: str) -> None:
    user = _get_user(session, actor, actor.user_id)
    if not verify_password(user.password_hash, current):
        raise Unauthorized("current password is wrong")
    _check_password(new)
    if verify_password(user.password_hash, new):
        raise Invalid("new password must differ from the current one")
    user.password_hash = hash_password(new)
    user.token_version += 1
    audit.record(session, actor, "auth.password_change", "user", user.id, {})
    session.commit()


def logout_all(session: Session, actor: Actor) -> None:
    user = _get_user(session, actor, actor.user_id)
    user.token_version += 1
    audit.record(session, actor, "auth.logout_all", "user", user.id, {})
    session.commit()


def update_branch(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    *,
    name: str | None,
    is_active: bool | None,
) -> Branch:
    actor.require(Perm.BRANCH_MANAGE)
    branch = session.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.tenant_id == actor.tenant_id)
    )
    if branch is None:
        raise NotFound("branch not found")
    before = {"name": branch.name, "is_active": branch.is_active}
    if name is not None:
        branch.name = name
    if is_active is not None:
        branch.is_active = is_active
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("branch name already exists") from exc
    audit.record(
        session,
        actor,
        "branch.update",
        "branch",
        branch.id,
        {
            "branch_id": str(branch.id),
            "before": before,
            "after": {"name": branch.name, "is_active": branch.is_active},
        },
    )
    session.commit()
    return branch
