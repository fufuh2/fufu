"""Platform CLI: python -m app.cli create-tenant --name X --owner-email Y (password via env)."""

import argparse
import os
import sys

from app.db import get_sessionmaker
from app.services.admin import bootstrap_tenant


def main() -> int:
    parser = argparse.ArgumentParser(prog="opsx")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ct = sub.add_parser("create-tenant")
    ct.add_argument("--name", required=True)
    ct.add_argument("--owner-email", required=True)
    ct.add_argument("--currency", default="TRY")
    args = parser.parse_args()
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
