"""Platform CLI.

create-tenant: python -m app.cli create-tenant --name X --owner-email Y
    (password from OPSX_OWNER_PASSWORD)
copy-from-if-empty: copies every table from OPSX_COPY_FROM_DATABASE_URL into the configured
    database, only when the target has no tenant yet. Used once to move to a new database host;
    remove the variable afterwards.
bootstrap-if-empty: creates the first tenant from OPSX_BOOTSTRAP_TENANT,
    OPSX_BOOTSTRAP_OWNER_EMAIL and OPSX_BOOTSTRAP_OWNER_PASSWORD only when the database has
    no tenant yet. Safe to run on every start (hosts without a shell); remove the variables
    once it has run.
"""

import argparse
import os
import sys

from sqlalchemy import Engine, create_engine, func, insert, select, text
from sqlalchemy.orm import Session

from app.config import normalize_database_url
from app.db import Base, get_engine, get_sessionmaker
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


def copy_from_if_empty(source: Engine, target: Engine) -> str:
    """One transaction on the target: either everything is copied or nothing is."""
    with source.connect() as src, target.begin() as dst:
        versions = [
            c.execute(text("SELECT version_num FROM alembic_version")).scalar() for c in (src, dst)
        ]
        if versions[0] != versions[1]:
            return f"refused: source schema {versions[0]} != target {versions[1]}"
        if (dst.scalar(select(func.count()).select_from(Tenant)) or 0) > 0:
            return "skipped: target already has data"
        counts = []
        for table in Base.metadata.sorted_tables:
            rows = [dict(r) for r in src.execute(select(table)).mappings()]
            if rows:
                dst.execute(insert(table), rows)
            counts.append(f"{table.name}={len(rows)}")
            for col in table.columns:
                if col.autoincrement is True or (
                    col.primary_key and col.type.python_type is int and len(table.primary_key) == 1
                ):
                    # Continue numbering after the copied ids. Names come from our own models.
                    seq = (
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col.name}'), "  # noqa: S608
                        f"COALESCE((SELECT MAX({col.name}) FROM {table.name}), 0) + 1, false)"
                    )
                    dst.execute(text(seq))
        return "copied " + " ".join(counts)


def main() -> int:
    parser = argparse.ArgumentParser(prog="opsx")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ct = sub.add_parser("create-tenant")
    ct.add_argument("--name", required=True)
    ct.add_argument("--owner-email", required=True)
    ct.add_argument("--currency", default="TRY")
    sub.add_parser("bootstrap-if-empty")
    sub.add_parser("copy-from-if-empty")
    args = parser.parse_args()
    if args.cmd == "copy-from-if-empty":
        url = os.environ.get("OPSX_COPY_FROM_DATABASE_URL", "")
        if not url:
            print("skipped: OPSX_COPY_FROM_DATABASE_URL not set")
            return 0
        result = copy_from_if_empty(create_engine(normalize_database_url(url)), get_engine())
        print(result)
        return 1 if result.startswith("refused") else 0
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
