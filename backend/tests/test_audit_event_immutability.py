"""P0 audit immutability — the append-only trigger on candidate_application_events.

The trigger is Postgres-only (a no-op on the sqlite test DB), so the structural
checks below run everywhere and the behavioural check runs only when a real
Postgres URL is provided via ``TEST_POSTGRES_URL``. The structural checks are
what actually gate CI (sqlite can't carry the trigger); the behavioural test is
opt-in local/CI-with-PG coverage that the trigger really rejects an UPDATE.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "143_audit_event_immutability.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "mig_143_audit_event_immutability", _MIGRATION
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_chains_off_bullhorn_head() -> None:
    mig = _load_migration()
    assert mig.revision == "143_audit_event_immutability"
    assert mig.down_revision == "142_add_bullhorn_integration"


def test_migration_defines_before_update_trigger_sql() -> None:
    text = _MIGRATION.read_text(encoding="utf-8")
    # The function raises on UPDATE, the trigger fires BEFORE UPDATE per row.
    assert "reject_candidate_application_event_update" in text
    assert "BEFORE UPDATE ON candidate_application_events" in text
    assert "FOR EACH ROW" in text
    assert "RAISE EXCEPTION" in text
    # DELETE must stay allowed (cascade deletes on application/org removal).
    assert "BEFORE DELETE" not in text


def test_migration_is_postgres_guarded() -> None:
    """Both upgrade and downgrade must short-circuit on non-postgres dialects
    so the sqlite test DB (and any non-PG bind) is a clean no-op."""
    text = _MIGRATION.read_text(encoding="utf-8")
    # The dialect guard appears once per direction.
    assert text.count('bind.dialect.name != "postgresql"') == 2


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="behavioural trigger test requires a real Postgres (TEST_POSTGRES_URL)",
)
def test_trigger_rejects_update_on_real_postgres() -> None:
    from sqlalchemy import create_engine, text as sql_text

    mig = _load_migration()
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "CREATE TABLE IF NOT EXISTS candidate_application_events "
                "(id serial primary key, event_type text)"
            )
        )
        # Apply the trigger DDL from the migration.
        conn.execute(sql_text(mig._CREATE_FN))
        conn.execute(sql_text(mig._CREATE_TRIGGER))
        conn.execute(
            sql_text(
                "INSERT INTO candidate_application_events (event_type) "
                "VALUES ('scored')"
            )
        )

    # UPDATE is rejected by the trigger. The failed statement aborts the
    # transaction, so roll back before continuing.
    with engine.connect() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                sql_text(
                    "UPDATE candidate_application_events SET event_type = 'x'"
                )
            )
        assert "append-only" in str(excinfo.value)
        conn.rollback()

    # DELETE stays permitted (fresh transaction).
    with engine.begin() as conn:
        conn.execute(sql_text("DELETE FROM candidate_application_events"))

    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "DROP TRIGGER IF EXISTS trg_candidate_application_events_no_update "
                "ON candidate_application_events"
            )
        )
        conn.execute(sql_text("DROP TABLE candidate_application_events"))
    engine.dispose()
