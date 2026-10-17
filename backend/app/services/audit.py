from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.security import Actor


def record(
    session: Session,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: object,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            data=data or {},
        )
    )
