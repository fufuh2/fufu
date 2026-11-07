"""Production entry point: migrations, the one-off start tasks and the web server in one
Python process. Running them as separate commands imported the app three times and made a
cold start on the free plan about 15-20 seconds slower."""

import os
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.cli import bootstrap_if_empty, copy_from_if_empty
from app.config import normalize_database_url
from app.db import get_engine, get_sessionmaker
from app.demo import ensure_demo
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[1]


def main() -> int:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")  # a failed migration stops the start, as before
    source = os.environ.get("OPSX_COPY_FROM_DATABASE_URL", "")
    if source:
        result = copy_from_if_empty(create_engine(normalize_database_url(source)), get_engine())
        print(result, flush=True)
        if result.startswith("refused"):
            return 1
    with get_sessionmaker()() as session:
        print(bootstrap_if_empty(session, dict(os.environ)), flush=True)
    demo_email, demo_password = (
        os.environ.get(k, "") for k in ("OPSX_DEMO_EMAIL", "OPSX_DEMO_PASSWORD")
    )
    if demo_email and demo_password:
        try:
            with get_sessionmaker()() as session:
                reset = os.environ.get("OPSX_DEMO_RESET", "")
                print(ensure_demo(session, demo_email, demo_password, reset=reset), flush=True)
        except Exception as exc:  # noqa: BLE001 - a demo problem must not stop the real system
            print(f"demo account not created: {exc}", flush=True)
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # noqa: S104 - the hosting platform routes traffic to this port
        port=int(os.environ.get("PORT", "10000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
