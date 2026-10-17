"""Generic POS webhook adapter.

Signature: X-Timestamp: <unix seconds>, X-Signature: sha256=<hex HMAC-SHA256(secret,
"<timestamp>." + raw body)>. Requests older or newer than 5 minutes are rejected (replay).
Every event is stored once in pos_events (inbox). Orders are created through the normal
order service with idempotency key "pos:<integration>:<external_id>", so a duplicate or
concurrent delivery can never create a second order or consume stock twice.
"""

import hashlib
import hmac
import os
import secrets
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.permissions import Perm, Role
from app.errors import Conflict, DomainError, Invalid, NotFound, Unauthorized
from app.models import (
    Integration,
    Order,
    PosEvent,
    PosProductMapping,
    User,
    UserBranchAccess,
)
from app.security import Actor, hash_password
from app.services import audit, orders
from app.services.admin import load_actor
from app.services.inventory import now
from app.services.scope import get_active_branch, get_product

MAX_SKEW_SECONDS = 300


class IntegrationsDisabled(DomainError):
    status_code = 503
    code = "integrations_disabled"


def _master_key() -> bytes:
    key = os.environ.get("OPSX_INTEGRATION_KEY", "")
    if len(key) < 32:
        raise IntegrationsDisabled("set OPSX_INTEGRATION_KEY (>= 32 chars) to enable integrations")
    return key.encode()


def derive_secret(integration: Integration) -> str:
    msg = f"{integration.id}:{integration.secret_version}".encode()
    return hmac.new(_master_key(), msg, hashlib.sha256).hexdigest()


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return (
        "sha256="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )


# --- management -----------------------------------------------------------------------------


def _get(session: Session, actor: Actor, integration_id: uuid.UUID) -> Integration:
    integ = session.scalar(
        select(Integration).where(
            Integration.id == integration_id, Integration.tenant_id == actor.tenant_id
        )
    )
    if integ is None:
        raise NotFound("integration not found")
    return integ


def create_integration(
    session: Session, actor: Actor, name: str, branch_id: uuid.UUID
) -> tuple[Integration, str]:
    actor.require(Perm.INTEGRATION_MANAGE)
    _master_key()
    get_active_branch(session, actor, branch_id)
    integ_id = uuid.uuid4()
    # A dedicated, non-interactive user: orders and stock movements are attributed to it.
    # Its address uses the reserved .invalid TLD, which login rejects, and the password is
    # a random value nobody knows.
    service_user = User(
        tenant_id=actor.tenant_id,
        email=f"pos-{integ_id}@integrations.invalid",
        password_hash=hash_password(secrets.token_urlsafe(48)),
        role=Role.SHIFT_LEAD.value,
    )
    session.add(service_user)
    session.flush()
    session.add(UserBranchAccess(user_id=service_user.id, branch_id=branch_id))
    integ = Integration(
        id=integ_id,
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        name=name,
        kind="pos_generic",
        service_user_id=service_user.id,
    )
    session.add(integ)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("integration name already exists") from exc
    audit.record(
        session,
        actor,
        "integration.create",
        "integration",
        integ.id,
        {"branch_id": str(branch_id), "name": name},
    )
    session.commit()
    return integ, derive_secret(integ)


def rotate_secret(session: Session, actor: Actor, integration_id: uuid.UUID) -> str:
    actor.require(Perm.INTEGRATION_MANAGE)
    integ = _get(session, actor, integration_id)
    integ.secret_version += 1
    audit.record(
        session,
        actor,
        "integration.rotate",
        "integration",
        integ.id,
        {"branch_id": str(integ.branch_id), "version": integ.secret_version},
    )
    session.commit()
    return derive_secret(integ)


def set_active(
    session: Session, actor: Actor, integration_id: uuid.UUID, is_active: bool
) -> Integration:
    actor.require(Perm.INTEGRATION_MANAGE)
    integ = _get(session, actor, integration_id)
    integ.is_active = is_active
    user = session.get(User, integ.service_user_id)
    if user is not None:
        user.is_active = is_active
    audit.record(
        session,
        actor,
        "integration.update",
        "integration",
        integ.id,
        {"branch_id": str(integ.branch_id), "is_active": is_active},
    )
    session.commit()
    return integ


def set_mappings(
    session: Session, actor: Actor, integration_id: uuid.UUID, mappings: dict[str, uuid.UUID]
) -> list[PosProductMapping]:
    actor.require(Perm.INTEGRATION_MANAGE)
    integ = _get(session, actor, integration_id)
    for code, product_id in mappings.items():
        product = get_product(session, actor, product_id)
        session.execute(
            pg_insert(PosProductMapping)
            .values(integration_id=integ.id, external_code=code, product_id=product.id)
            .on_conflict_do_update(
                index_elements=["integration_id", "external_code"],
                set_={"product_id": product.id},
            )
        )
    audit.record(
        session,
        actor,
        "integration.mappings",
        "integration",
        integ.id,
        {"branch_id": str(integ.branch_id), "codes": sorted(mappings)},
    )
    session.commit()
    return list(
        session.scalars(
            select(PosProductMapping).where(PosProductMapping.integration_id == integ.id)
        )
    )


def list_events(
    session: Session, actor: Actor, integration_id: uuid.UUID, status: str | None
) -> list[PosEvent]:
    actor.require(Perm.INTEGRATION_MANAGE)
    integ = _get(session, actor, integration_id)
    stmt = (
        select(PosEvent)
        .where(PosEvent.integration_id == integ.id)
        .order_by(PosEvent.received_at.desc())
        .limit(200)
    )
    if status:
        stmt = stmt.where(PosEvent.status == status)
    return list(session.scalars(stmt))


# --- inbound --------------------------------------------------------------------------------


def authenticate_delivery(
    session: Session,
    integration_id: uuid.UUID,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
) -> Integration:
    """Every failure is the same 401 so callers cannot probe for integration ids."""
    _master_key()
    integ = session.get(Integration, integration_id)
    try:
        ts = int(timestamp or "")
    except ValueError:
        ts = 0
    expected = sign(derive_secret(integ), timestamp or "", body) if integ else "x"
    ok = (
        integ is not None
        and integ.is_active
        and signature is not None
        and hmac.compare_digest(expected, signature)
        and abs(time.time() - ts) <= MAX_SKEW_SECONDS
    )
    if not ok or integ is None:
        raise Unauthorized("invalid signature")
    return integ


def _store(
    session: Session,
    integ: Integration,
    payload: dict[str, Any],
    status: str,
    error: str | None,
    order_id: uuid.UUID | None,
) -> PosEvent:
    """Insert or update the inbox row; a concurrent duplicate resolves to one row."""
    stmt = (
        pg_insert(PosEvent)
        .values(
            id=uuid.uuid4(),
            integration_id=integ.id,
            external_id=payload["external_id"],
            event=payload["event"],
            payload=payload,
            status=status,
            error=error,
            order_id=order_id,
            processed_at=now() if status == "processed" else None,
        )
        .on_conflict_do_update(
            constraint="uq_pos_event",
            set_={
                "payload": payload,
                "status": status,
                "error": error,
                "order_id": order_id,
                "attempts": PosEvent.attempts + 1,
                "processed_at": now() if status == "processed" else None,
            },
        )
        .returning(PosEvent.id)
    )
    event_id = session.execute(stmt).scalar_one()
    session.commit()
    event = session.get(PosEvent, event_id, populate_existing=True)
    if event is None:  # pragma: no cover - just inserted
        raise RuntimeError("inbox row missing")
    return event


def _order_key(integ: Integration, external_id: str) -> str:
    return f"pos:{integ.id}:{external_id}"


def process(session: Session, integ: Integration, payload: dict[str, Any]) -> tuple[PosEvent, bool]:
    """Returns (event, duplicate). Business failures are stored as failed events and
    re-raised so the caller answers 422; a later delivery or a retry reprocesses them."""
    existing = session.scalar(
        select(PosEvent).where(
            PosEvent.integration_id == integ.id,
            PosEvent.external_id == payload["external_id"],
            PosEvent.event == payload["event"],
        )
    )
    if existing is not None and existing.status == "processed":
        if existing.payload != payload:
            raise Invalid("event was already processed with a different payload")
        return existing, True
    actor = load_actor(session, integ.service_user_id)
    if actor is None:
        raise Unauthorized("integration user is inactive")
    try:
        order_id = _apply(session, actor, integ, payload)
    except DomainError as exc:
        session.rollback()
        _store(session, integ, payload, "failed", exc.message[:500], None)
        raise
    return _store(session, integ, payload, "processed", None, order_id), False


def _apply(
    session: Session, actor: Actor, integ: Integration, payload: dict[str, Any]
) -> uuid.UUID:
    key = _order_key(integ, payload["external_id"])
    if payload["event"] == "order.cancelled":
        order = session.scalar(
            select(Order).where(Order.tenant_id == actor.tenant_id, Order.idempotency_key == key)
        )
        if order is None:
            raise Invalid("order not received yet; the cancel will be retried")
        if order.status != "cancelled":
            orders.cancel_order(session, actor, order.id, payload.get("reason") or "POS cancel")
        return order.id
    mapped = {
        m.external_code: m.product_id
        for m in session.scalars(
            select(PosProductMapping).where(PosProductMapping.integration_id == integ.id)
        )
    }
    lines = []
    for line in payload["lines"]:
        product_id = mapped.get(line["code"])
        if product_id is None:
            raise Invalid(f"unmapped POS product code '{line['code']}'")
        lines.append(
            orders.OrderLineIn(
                product_id, int(line["quantity"]), Decimal(str(line.get("discount", "0")))
            )
        )
    order, _ = orders.create_order(
        session,
        actor,
        orders.OrderIn(
            branch_id=integ.branch_id,
            idempotency_key=key,
            channel="pos",
            external_ref=payload["external_id"],
            ordered_at=datetime.fromisoformat(payload["ordered_at"]),
            lines=lines,
        ),
    )
    return order.id


def retry(
    session: Session, actor: Actor, integration_id: uuid.UUID, event_id: uuid.UUID
) -> tuple[PosEvent, bool]:
    actor.require(Perm.INTEGRATION_MANAGE)
    integ = _get(session, actor, integration_id)
    event = session.scalar(
        select(PosEvent).where(PosEvent.id == event_id, PosEvent.integration_id == integ.id)
    )
    if event is None:
        raise NotFound("event not found")
    return process(session, integ, dict(event.payload))
