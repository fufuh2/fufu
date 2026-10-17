"""The migrated schema must contain every constraint and index the models declare.
`alembic check` does not compare CHECK constraints, so this test does."""

from sqlalchemy import CheckConstraint, inspect
from sqlalchemy.orm import Session

from app.db import Base, get_engine


def test_check_constraints_match_models(session: Session) -> None:
    insp = inspect(get_engine())
    missing = []
    for table in Base.metadata.sorted_tables:
        declared = {c.name for c in table.constraints if isinstance(c, CheckConstraint) and c.name}
        actual = {c["name"] for c in insp.get_check_constraints(table.name)}
        missing += [(table.name, n) for n in declared - actual]
    assert missing == []


def test_append_only_triggers_present(session: Session) -> None:
    from sqlalchemy import text

    rows = session.execute(
        text("SELECT tgrelid::regclass::text FROM pg_trigger WHERE tgname LIKE '%append_only'")
    ).scalars()
    assert {"stock_movements", "audit_events"} <= set(rows)
