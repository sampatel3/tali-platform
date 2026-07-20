from __future__ import annotations

import importlib.util
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.models.agent_run import AgentRun


_DUE_INDEX = "ix_agent_runs_terminal_event_retry_due"


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "183_add_agent_run_event_retry.py"
    )
    spec = importlib.util.spec_from_file_location("agent_run_event_retry_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_run_event_retry_migration_preserves_rows_and_old_worker_inserts(
    monkeypatch,
):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    runs = sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    finished_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    historical_finished_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            runs.insert(),
            [
                {
                    "id": 0,
                    "status": "failed",
                    "finished_at": historical_finished_at,
                },
                {"id": 1, "status": "failed", "finished_at": finished_at},
            ],
        )
        migration = _load_migration()
        assert migration.revision == "183_agent_run_event_retry"
        assert migration.down_revision == "182_candidate_clipboard"
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        upgraded = sa.table(
            "agent_runs",
            sa.column("id", sa.BigInteger),
            sa.column("status", sa.String),
            sa.column("finished_at", sa.DateTime(timezone=True)),
            sa.column("terminal_event_failure_count", sa.Integer),
            sa.column(
                "terminal_event_reconciled_at",
                sa.DateTime(timezone=True),
            ),
        )
        connection.execute(
            upgraded.insert(),
            {"id": 2, "status": "failed", "finished_at": finished_at},
        )
        rows = connection.execute(
            sa.select(
                upgraded.c.id,
                upgraded.c.terminal_event_failure_count,
                upgraded.c.terminal_event_reconciled_at,
            ).order_by(upgraded.c.id)
        ).all()
        index_sql = connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = :name"
            ),
            {"name": _DUE_INDEX},
        )

        migration.downgrade()
        downgraded = sa.Table("agent_runs", sa.MetaData(), autoload_with=connection)
        preserved_ids = connection.scalars(
            sa.select(downgraded.c.id).order_by(downgraded.c.id)
        ).all()
        downgraded_index_sql = connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = :name"
            ),
            {"name": _DUE_INDEX},
        )

    assert rows == [
        (0, 0, historical_finished_at.replace(tzinfo=None)),
        (1, 0, None),
        (2, 0, None),
    ]
    normalized_index_sql = " ".join(str(index_sql).lower().split())
    assert (
        "(coalesce(terminal_event_next_attempt_at, finished_at), id)"
        in normalized_index_sql
    )
    assert downgraded_index_sql is None
    assert preserved_ids == [0, 1, 2]
    assert "terminal_event_reconciled_at" not in downgraded.c
    assert "terminal_event_failure_count" not in downgraded.c


def test_agent_run_retry_index_matches_postgres_selector_order():
    index = next(index for index in AgentRun.__table__.indexes if index.name == _DUE_INDEX)
    ddl = " ".join(
        str(CreateIndex(index).compile(dialect=postgresql.dialect())).lower().split()
    )

    assert (
        "(coalesce(terminal_event_next_attempt_at, finished_at), id)" in ddl
    )
    assert "terminal_event_reconciled_at is null" in ddl
    assert "status in ('failed', 'aborted', 'budget_paused')" in ddl


def _plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(child)


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="query-plan test requires PostgreSQL (TEST_POSTGRES_URL)",
)
def test_retry_selector_uses_expression_index_without_sort_on_postgres(monkeypatch):
    """Exercise the migration and exact production selector on real PostgreSQL."""

    schema = f"agent_run_retry_plan_{uuid.uuid4().hex}"
    engine = sa.create_engine(os.environ["TEST_POSTGRES_URL"])
    migration = _load_migration()

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            connection.exec_driver_sql(
                "CREATE TABLE agent_runs ("
                "id BIGINT PRIMARY KEY, "
                "status VARCHAR NOT NULL, "
                "finished_at TIMESTAMPTZ NULL"
                ")"
            )
            monkeypatch.setattr(
                migration,
                "op",
                Operations(MigrationContext.configure(connection)),
            )
            migration.upgrade()
            connection.exec_driver_sql(
                "INSERT INTO agent_runs (id, status, finished_at) "
                "SELECT value, 'failed', now() - value * interval '1 second' "
                "FROM generate_series(1, 20000) AS value"
            )
            connection.exec_driver_sql("ANALYZE agent_runs")
            connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
            raw_plan = connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, FORMAT JSON) "
                "SELECT id FROM agent_runs "
                "WHERE status IN ('failed', 'aborted', 'budget_paused') "
                "AND finished_at IS NOT NULL "
                "AND terminal_event_reconciled_at IS NULL "
                "AND (terminal_event_next_attempt_at IS NULL "
                "OR terminal_event_next_attempt_at <= now()) "
                "ORDER BY COALESCE(terminal_event_next_attempt_at, finished_at), id "
                "LIMIT 200 FOR UPDATE OF agent_runs SKIP LOCKED"
            ).scalar_one()
            plan_document = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
            nodes = list(_plan_nodes(plan_document[0]["Plan"]))

            assert any(node.get("Index Name") == _DUE_INDEX for node in nodes)
            assert all(node.get("Node Type") != "Sort" for node in nodes)

            migration.downgrade()
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        engine.dispose()
