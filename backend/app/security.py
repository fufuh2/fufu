import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.domain.permissions import TENANT_WIDE_ROLES, Perm, Role, has_perm
from app.errors import Forbidden

_hasher = PasswordHasher()
# Verified against when the e-mail is unknown, so response time does not reveal which
# accounts exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except VerificationError:
        return False


def issue_token(user_id: uuid.UUID, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> uuid.UUID:
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    return uuid.UUID(payload["sub"])


@dataclass(frozen=True)
class Actor:
    """The authenticated caller. Tenant and branch scope come from the DB, never the client."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role
    branch_ids: frozenset[uuid.UUID] | None  # None = every branch of the tenant

    def require(self, perm: Perm) -> None:
        if not has_perm(self.role, perm):
            raise Forbidden(f"missing permission {perm}")

    @property
    def tenant_wide(self) -> bool:
        return self.role in TENANT_WIDE_ROLES
