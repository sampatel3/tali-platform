"""Revision 186 retains all 184/185 evidence while adding graph history."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration(filename: str, module_name: str):
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fingerprint(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def test_upgrade_from_185_preserves_assessment_and_graph_evidence_exactly():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    assessments = sa.Table(
        "assessments",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String, nullable=False),
        sa.Column("posted_to_workable", sa.Boolean, nullable=False),
    )
    with engine.connect() as connection:
        metadata.create_all(connection)
        connection.execute(
            assessments.insert(),
            {"id": 77, "token": "assessment-evidence", "posted_to_workable": False},
        )
        connection.commit()
        context = MigrationContext.configure(connection)

        revision_184 = _migration(
            "184_add_assessment_result_delivery_receipt.py",
            "assessment_result_delivery_184_for_186",
        )
        revision_184.op = Operations(context)
        revision_184.upgrade()
        connection.execute(
            sa.text(
                "UPDATE assessments SET workable_result_delivery_status = :status, "
                "workable_result_delivery_receipt = :receipt WHERE id = 77"
            ),
            {
                "status": "reconciliation_required",
                "receipt": json.dumps(
                    {
                        "operation_id": "assessment-op",
                        "provider_outcome_uncertain": True,
                    }
                ),
            },
        )

        revision_185 = _migration(
            "185_add_graph_ingest_dispatch_outbox.py",
            "graph_ingest_dispatch_185_for_186",
        )
        revision_185.op = Operations(context)
        revision_185.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO graph_ingest_dispatches "
                "(operation_id, organization_id, work_kind, entity_id, source_refs, "
                "status, dispatch_attempts, dispatch_nonce, worker_attempt_nonce, "
                "provider_attempt_started_at, completed_at, last_error_code) VALUES "
                "(:operation_id, 42, 'candidate', 9, :source_refs, "
                "'reconciliation_required', 3, :dispatch_nonce, :worker_nonce, "
                ":provider_started, :completed, :error_code)"
            ),
            {
                "operation_id": "11111111-1111-4111-8111-111111111111",
                "source_refs": json.dumps([{"kind": "candidate", "id": 9}]),
                "dispatch_nonce": "22222222-2222-4222-8222-222222222222",
                "worker_nonce": "33333333-3333-4333-8333-333333333333",
                "provider_started": "2026-07-17T01:02:03+00:00",
                "completed": "2026-07-17T01:12:03+00:00",
                "error_code": "provider_outcome_ambiguous:TimeoutError",
            },
        )
        connection.commit()

        graph_columns = (
            "operation_id, organization_id, work_kind, entity_id, source_refs, "
            "status, dispatch_attempts, dispatch_nonce, worker_attempt_nonce, "
            "provider_attempt_started_at, completed_at, last_error_code"
        )
        before_graph = dict(
            connection.execute(
                sa.text(
                    f"SELECT {graph_columns} FROM graph_ingest_dispatches "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            .mappings()
            .one()
        )
        before_fingerprint = _fingerprint(before_graph)
        before_assessment = connection.execute(
            sa.text(
                "SELECT token, workable_result_delivery_status, "
                "workable_result_delivery_receipt FROM assessments WHERE id = 77"
            )
        ).one()

        revision_186 = _migration(
            "186_add_graph_ingest_reconciliation_history.py",
            "graph_ingest_reconciliation_186",
        )
        assert revision_186.down_revision == "185_graph_ingest_dispatch"
        revision_186.op = Operations(context)
        revision_186.upgrade()
        connection.commit()

        assert "reconciliation_history" in {
            column["name"]
            for column in sa.inspect(connection).get_columns("graph_ingest_dispatches")
        }
        assert "ix_graph_ingest_dispatches_reconciliation" in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("graph_ingest_dispatches")
        }
        after_graph = dict(
            connection.execute(
                sa.text(
                    f"SELECT {graph_columns} FROM graph_ingest_dispatches "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            .mappings()
            .one()
        )
        assert _fingerprint(after_graph) == before_fingerprint
        assert (
            connection.execute(
                sa.text(
                    "SELECT token, workable_result_delivery_status, "
                    "workable_result_delivery_receipt FROM assessments WHERE id = 77"
                )
            ).one()
            == before_assessment
        )

        retained_history = json.dumps([{"retained": True}])
        connection.execute(
            sa.text(
                "UPDATE graph_ingest_dispatches SET reconciliation_history = :history "
                "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
            ),
            {"history": retained_history},
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="must not be deleted"):
            revision_186.downgrade()
        assert (
            connection.execute(
                sa.text(
                    "SELECT reconciliation_history FROM graph_ingest_dispatches "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                )
            ).scalar_one()
            == retained_history
        )

        graph_evidence_columns = f"{graph_columns}, reconciliation_history"
        before_revision_187 = dict(
            connection.execute(
                sa.text(
                    f"SELECT {graph_evidence_columns} FROM graph_ingest_dispatches "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            .mappings()
            .one()
        )
        before_revision_187_fingerprint = _fingerprint(before_revision_187)

        revision_187 = _migration(
            "187_add_graph_ingest_operation_manifest.py",
            "graph_ingest_manifest_187",
        )
        assert revision_187.down_revision == "186_graph_ingest_reconciliation"
        revision_187.op = Operations(context)
        revision_187.upgrade()
        connection.commit()

        graph_column_names = {
            column["name"]
            for column in sa.inspect(connection).get_columns("graph_ingest_dispatches")
        }
        assert {
            "operation_manifest",
            "operation_manifest_sha256",
        }.issubset(graph_column_names)
        assert "ck_graph_ingest_dispatches_manifest_pair" in {
            constraint["name"]
            for constraint in sa.inspect(connection).get_check_constraints(
                "graph_ingest_dispatches"
            )
        }
        after_revision_187 = dict(
            connection.execute(
                sa.text(
                    f"SELECT {graph_evidence_columns} FROM graph_ingest_dispatches "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            .mappings()
            .one()
        )
        assert _fingerprint(after_revision_187) == before_revision_187_fingerprint
        assert connection.execute(
            sa.text(
                "SELECT operation_manifest, operation_manifest_sha256 "
                "FROM graph_ingest_dispatches "
                "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
            )
        ).one() == (None, None)
        assert (
            connection.execute(
                sa.text(
                    "SELECT token, workable_result_delivery_status, "
                    "workable_result_delivery_receipt FROM assessments WHERE id = 77"
                )
            ).one()
            == before_assessment
        )

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "UPDATE graph_ingest_dispatches "
                    "SET operation_manifest = :manifest "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                ),
                {"manifest": json.dumps({"version": 1})},
            )
        connection.rollback()
        with pytest.raises(RuntimeError, match="must not be deleted"):
            revision_187.downgrade()
        assert dict(
            connection.execute(
                sa.text(
                    f"SELECT {graph_evidence_columns} FROM graph_ingest_dispatches "
                    "WHERE operation_id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            .mappings()
            .one()
        ) == after_revision_187
