import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_ttl_minutes: int
    env: str


def normalize_database_url(url: str) -> str:
    """Hosting providers (e.g. Render) hand out postgres:// or postgresql:// URLs; SQLAlchemy
    needs the driver named explicitly."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def load_settings() -> Settings:
    env = os.environ.get("OPSX_ENV", "production")
    secret = os.environ.get("OPSX_JWT_SECRET", "")
    if len(secret) < 32:
        # Refuse to run with a weak or missing signing key; tests set their own value.
        raise RuntimeError("OPSX_JWT_SECRET must be set and at least 32 characters long")
    database_url = os.environ.get("OPSX_DATABASE_URL", "")
    if not database_url:
        # No built-in default: a forgotten variable must not silently use dev credentials.
        raise RuntimeError("OPSX_DATABASE_URL must be set")
    return Settings(
        database_url=normalize_database_url(database_url),
        jwt_secret=secret,
        jwt_ttl_minutes=int(os.environ.get("OPSX_JWT_TTL_MINUTES", "30")),
        env=env,
    )
