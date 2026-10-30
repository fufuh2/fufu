"""Moving to a new database host: copy-from-if-empty."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.cli import copy_from_if_empty
from app.db import get_engine
from tests.conftest import BACKEND, TEST_DB, new_tenant, setup_branch

TARGET = TEST_DB.rsplit("/", 1)[0] + "/opsx_copy_target_test"


def _fresh_target():  # type: ignore[no-untyped-def]
    from alembic import command
    from alembic.config import Config

    admin = create_engine(TEST_DB.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS opsx_copy_target_test"))
        c.execute(text("CREATE DATABASE opsx_copy_target_test"))
    admin.dispose()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TARGET)
    import os

    old = os.environ["OPSX_DATABASE_URL"]
    os.environ["OPSX_DATABASE_URL"] = TARGET
    try:
        command.upgrade(cfg, "head")
    finally:
        os.environ["OPSX_DATABASE_URL"] = old
    return create_engine(TARGET)


def test_copy_moves_everything_once(client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    item = api.get("/count-items").json()[0]["id"]
    api.put(
        f"/branches/{branch}/monthly-counts/2026-08",
        {"counted_by_name": "Ali", "lines": [{"item_id": item, "waste": "2", "counted": "5"}]},
    )
    target = _fresh_target()
    try:
        result = copy_from_if_empty(get_engine(), target)
        assert result.startswith("copied ") and "count_items=98" in result
        with target.connect() as c:
            assert c.execute(text("SELECT counted_by_name FROM monthly_counts")).scalar() == "Ali"
            assert c.execute(text("SELECT count(*) FROM monthly_count_lines")).scalar() == 98
            n = c.execute(text("SELECT count(*) FROM audit_events")).scalar()
            assert n == session.execute(text("SELECT count(*) FROM audit_events")).scalar()
        # sequences continue after the copied ids: a new audit row does not collide
        with target.begin() as c:
            c.execute(
                text("INSERT INTO audit_events(action, entity_type, data) VALUES ('t','t','{}')")
            )
        assert copy_from_if_empty(get_engine(), target) == "skipped: target already has data"
    finally:
        target.dispose()
