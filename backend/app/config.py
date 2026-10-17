import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_ttl_minutes: int
    env: str


def load_settings() -> Settings:
    env = os.environ.get("OPSX_ENV", "development")
    secret = os.environ.get("OPSX_JWT_SECRET", "")
    if len(secret) < 32:
        # Refuse to run with a weak or missing signing key; tests set their own value.
        raise RuntimeError("OPSX_JWT_SECRET must be set and at least 32 characters long")
    return Settings(
        database_url=os.environ.get(
            "OPSX_DATABASE_URL", "postgresql+psycopg://ops:ops@localhost:5432/opsx"
        ),
        jwt_secret=secret,
        jwt_ttl_minutes=int(os.environ.get("OPSX_JWT_TTL_MINUTES", "30")),
        env=env,
    )
