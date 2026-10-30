"""Integration tests run against a real, disposable PostgreSQL database.

OPSX_TEST_DATABASE_URL must point to a database whose contents may be destroyed; the
fixture refuses to run against a name that does not end in '_test'.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

TEST_DB = os.environ.get(
    "OPSX_TEST_DATABASE_URL", "postgresql+psycopg://ops:ops@localhost:5432/opsx_test"
)
os.environ["OPSX_DATABASE_URL"] = TEST_DB
os.environ.setdefault("OPSX_JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("OPSX_ENV", "test")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import Base, get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.admin import bootstrap_tenant  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    if not TEST_DB.rsplit("/", 1)[-1].endswith("_test"):
        raise RuntimeError(f"refusing to run destructive tests against {TEST_DB}")
    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session() -> Iterator[Session]:
    with get_sessionmaker()() as s:
        yield s


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class Api:
    """Small helper around TestClient that logs in and carries the bearer token."""

    def __init__(self, client: TestClient, email: str, password: str = PASSWORD):
        self.client = client
        res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, res.text
        self.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get("/api/v1" + path, headers=self.headers, **kw)

    def post(self, path: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        return self.client.post(
            "/api/v1" + path, json=json, headers={**self.headers, **(headers or {})}
        )

    def put(self, path: str, json: Any = None) -> Any:
        return self.client.put("/api/v1" + path, json=json, headers=self.headers)

    def patch(self, path: str, json: Any = None) -> Any:
        return self.client.patch("/api/v1" + path, json=json, headers=self.headers)


def new_tenant(session: Session, client: TestClient, name: str = "Acme") -> Api:
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    bootstrap_tenant(session, name=name, owner_email=email, owner_password=PASSWORD)
    return Api(client, email)


def setup_branch(api: Api, name: str = "Kadikoy") -> str:
    """A branch with the 98 products of the Taff Burger count form."""
    branch = api.post("/branches", {"name": name}).json()["id"]
    res = api.post("/count-items/load-defaults")
    assert res.status_code == 200, res.text
    return branch
