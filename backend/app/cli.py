"""Platform CLI.

create-tenant: python -m app.cli create-tenant --name X --owner-email Y
    (password from OPSX_OWNER_PASSWORD)
bootstrap-if-empty: creates the first tenant from OPSX_BOOTSTRAP_TENANT,
    OPSX_BOOTSTRAP_OWNER_EMAIL and OPSX_BOOTSTRAP_OWNER_PASSWORD only when the database has
    no tenant yet. Safe to run on every start (hosts without a shell); remove the variables
    once it has run.
"""

import argparse
import os
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import Tenant
from app.services.admin import bootstrap_tenant

BOOTSTRAP_VARS = (
    "OPSX_BOOTSTRAP_TENANT",
    "OPSX_BOOTSTRAP_OWNER_EMAIL",
    "OPSX_BOOTSTRAP_OWNER_PASSWORD",
)


def bootstrap_if_empty(session: Session, env: dict[str, str]) -> str:
    """Returns what happened; never prints or returns the password."""
    values = [env.get(k, "") for k in BOOTSTRAP_VARS]
    if not all(values):
        return "skipped: bootstrap variables not set"
    if (session.scalar(select(func.count()).select_from(Tenant)) or 0) > 0:
        return "skipped: a tenant already exists"
    tenant, owner = bootstrap_tenant(
        session, name=values[0], owner_email=values[1], owner_password=values[2]
    )
    return f"created tenant={tenant.id} owner={owner.id}"


def main() -> int:
    parser = argparse.ArgumentParser(prog="opsx")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ct = sub.add_parser("create-tenant")
    ct.add_argument("--name", required=True)
    ct.add_argument("--owner-email", required=True)
    ct.add_argument("--currency", default="TRY")
    sub.add_parser("bootstrap-if-empty")
    args = parser.parse_args()
    if args.cmd == "bootstrap-if-empty":
        with get_sessionmaker()() as session:
            print(bootstrap_if_empty(session, dict(os.environ)))
        return 0
    password = os.environ.get("OPSX_OWNER_PASSWORD")
    if not password:
        print("set OPSX_OWNER_PASSWORD (kept out of argv/shell history)", file=sys.stderr)
        return 2
    with get_sessionmaker()() as session:
        tenant, owner = bootstrap_tenant(
            session,
            name=args.name,
            owner_email=args.owner_email,
            owner_password=password,
            currency=args.currency,
        )
    print(f"tenant={tenant.id} owner={owner.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
