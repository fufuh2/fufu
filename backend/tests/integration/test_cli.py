from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import bootstrap_if_empty
from app.models import Tenant, User

ENV = {
    "OPSX_BOOTSTRAP_TENANT": "Prod Co",
    "OPSX_BOOTSTRAP_OWNER_EMAIL": "owner@example.com",
    "OPSX_BOOTSTRAP_OWNER_PASSWORD": "a-strong-initial-pass",
}


def test_bootstrap_only_once_and_never_echoes_password(session: Session) -> None:
    assert bootstrap_if_empty(session, {}).startswith("skipped: bootstrap variables")
    out = bootstrap_if_empty(session, ENV)
    assert out.startswith("created") and ENV["OPSX_BOOTSTRAP_OWNER_PASSWORD"] not in out
    again = bootstrap_if_empty(session, {**ENV, "OPSX_BOOTSTRAP_TENANT": "Other"})
    assert again == "skipped: a tenant already exists"
    assert session.scalar(select(func.count()).select_from(Tenant)) == 1
    assert session.scalar(select(User.role)) == "owner"
