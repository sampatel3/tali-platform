"""Revision 187 adds paired immutable graph payload evidence without data loss."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


def _migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "187_add_graph_ingest_operation_manifest.py"
    )
    spec = importlib.util.spec_from_file_location("graph_manifest_187", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_187_preserves_legacy_rows_and_enforces_the_manifest_pair():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    graph_dispatches = sa.Table(
        "graph_ingest_dispatches",
        metadata,
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.Integer, nullable=True),
        sa.Column("work_kind", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("source_refs", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reconciliation_history", sa.JSON, nullable=True),
    )
    with engine.connect() as connection:
        metadata.create_all(connection)
        connection.execute(
            graph_dispatches.insert(),
            {
                "operation_id": "11111111-1111-4111-8111-111111111111",
                "organization_id": 42,
                "work_kind": "candidate",
                "entity_id": 9,
                "source_refs": [{"kind": "candidate", "id": 9}],
                "status": "reconciliation_required",
                "reconciliation_history": [{"retained": True}],
            },
        )
        connection.commit()

        revision = _migration()
        assert revision.down_revision == "186_graph_ingest_reconciliation"
        revision.op = Operations(MigrationContext.configure(connection))
        revision.upgrade()
        connection.commit()

        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "graph_ingest_dispatches"
            )
        }
        assert {"operation_manifest", "operation_manifest_sha256"} <= columns
        checks = {
            constraint["name"]
            for constraint in sa.inspect(connection).get_check_constraints(
                "graph_ingest_dispatches"
            )
        }
        assert "ck_graph_ingest_dispatches_manifest_pair" in checks
        retained = connection.execute(
            sa.text(
                "SELECT reconciliation_history, operation_manifest, "
                "operation_manifest_sha256 FROM graph_ingest_dispatches "
                "WHERE operation_id = :operation_id"
            ),
            {"operation_id": "11111111-1111-4111-8111-111111111111"},
        ).one()
        assert json.loads(retained.reconciliation_history) == [{"retained": True}]
        assert retained.operation_manifest is None
        assert retained.operation_manifest_sha256 is None

        connection.commit()
        with pytest.raises(IntegrityError):
            with connection.begin():
                connection.execute(
                    sa.text(
                        "UPDATE graph_ingest_dispatches "
                        "SET operation_manifest = :manifest "
                        "WHERE operation_id = :operation_id"
                    ),
                    {
                        "manifest": json.dumps({"version": 1}),
                        "operation_id": "11111111-1111-4111-8111-111111111111",
                    },
                )

        connection.rollback()
        with pytest.raises(RuntimeError, match="must not be deleted"):
            revision.downgrade()


def test_model_explicit_none_uses_sql_null_for_legacy_manifest_pair():
    from app.models.graph_ingest_dispatch import GraphIngestDispatch
    from app.platform.database import Base

    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sa.orm.Session(engine)
    try:
        row = GraphIngestDispatch(
            operation_id="22222222-2222-4222-8222-222222222222",
            organization_id=42,
            work_kind="candidate",
            entity_id=9,
            source_refs=[{"kind": "candidate", "id": 9}],
            operation_manifest=None,
            operation_manifest_sha256=None,
        )
        session.add(row)
        session.commit()
        stored = session.execute(
            sa.text(
                "SELECT operation_manifest, operation_manifest_sha256 "
                "FROM graph_ingest_dispatches WHERE operation_id = :operation_id"
            ),
            {"operation_id": row.operation_id},
        ).one()
        assert stored == (None, None)
    finally:
        session.close()
        engine.dispose()
