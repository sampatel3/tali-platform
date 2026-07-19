"""Revision 185 adds graph dispatch evidence without destructive rollback."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "185_add_graph_ingest_dispatch_outbox.py"
    )
    spec = importlib.util.spec_from_file_location("graph_ingest_dispatch_185", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_is_additive_and_downgrade_preserves_dispatch_evidence():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    existing = sa.Table(
        "existing_source",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("value", sa.String, nullable=False),
    )
    with engine.connect() as connection:
        metadata.create_all(connection)
        connection.execute(existing.insert(), {"id": 1, "value": "preserve"})
        connection.commit()
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()
        connection.commit()

        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "graph_ingest_dispatches"
            )
        }
        assert {
            "operation_id",
            "organization_id",
            "work_kind",
            "entity_id",
            "source_refs",
            "status",
            "dispatch_nonce",
            "worker_attempt_nonce",
            "provider_attempt_started_at",
            "last_error_code",
        } <= columns
        assert {
            "ix_graph_ingest_dispatches_recovery",
            "ix_graph_ingest_dispatches_entity",
        } <= {
            index["name"]
            for index in sa.inspect(connection).get_indexes(
                "graph_ingest_dispatches"
            )
        }
        assert {
            "ck_graph_ingest_dispatches_work_kind",
            "ck_graph_ingest_dispatches_status",
        } <= {
            constraint["name"]
            for constraint in sa.inspect(connection).get_check_constraints(
                "graph_ingest_dispatches"
            )
        }
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO graph_ingest_dispatches "
                    "(operation_id, work_kind, entity_id, source_refs) "
                    "VALUES ('bad-kind', 'unknown', 9, '[]')"
                )
            )
        connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO graph_ingest_dispatches "
                    "(operation_id, work_kind, entity_id, source_refs, status) "
                    "VALUES ('bad-status', 'candidate', 9, '[]', 'unknown')"
                )
            )
        connection.rollback()
        connection.execute(
            sa.text(
                "INSERT INTO graph_ingest_dispatches "
                "(operation_id, work_kind, entity_id, source_refs) "
                "VALUES ('op-1', 'candidate', 9, '[]')"
            )
        )
        connection.commit()

        with pytest.raises(RuntimeError, match="must not be deleted"):
            migration.downgrade()

        assert connection.execute(
            sa.text(
                "SELECT work_kind, entity_id FROM graph_ingest_dispatches "
                "WHERE operation_id = 'op-1'"
            )
        ).one() == ("candidate", 9)
        assert connection.execute(
            sa.text("SELECT value FROM existing_source WHERE id = 1")
        ).scalar_one() == "preserve"
