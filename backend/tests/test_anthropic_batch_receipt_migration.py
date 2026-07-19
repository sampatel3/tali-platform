"""Revision 188 normalizes receipts without rewriting legacy evidence."""

from __future__ import annotations

import importlib.util
import json
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
        / "188_normalize_anthropic_batch_receipts.py"
    )
    spec = importlib.util.spec_from_file_location("batch_receipts_188", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_188_preserves_legacy_json_and_enforces_receipt_identity():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    batch_jobs = sa.Table(
        "anthropic_batch_jobs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("context", sa.JSON, nullable=True),
    )
    usage_events = sa.Table(
        "usage_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("metadata", sa.JSON, nullable=True),
    )
    call_logs = sa.Table(
        "claude_call_log",
        metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("anthropic_request_id", sa.String, nullable=True),
        sa.Column("feature_hint", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="ok"),
    )
    legacy_context = {
        "cvparse-1": {"entity_id": "application:1"},
        "_metered_results": {
            "cvparse-1": {"state": "skipped", "result_type": "errored"}
        },
    }
    with engine.connect() as connection:
        metadata.create_all(connection)
        connection.execute(batch_jobs.insert(), {"id": 7, "context": legacy_context})
        connection.execute(usage_events.insert(), {"id": 11})
        connection.execute(call_logs.insert(), {"id": 13})
        connection.commit()

        revision = _migration()
        assert revision.down_revision == "187_graph_ingest_manifest"
        revision.op = Operations(MigrationContext.configure(connection))
        revision.upgrade()
        connection.commit()

        inspector = sa.inspect(connection)
        assert inspector.has_table("anthropic_batch_result_receipts")
        assert "ix_anthropic_batch_result_receipts_id" not in {
            index["name"]
            for index in inspector.get_indexes("anthropic_batch_result_receipts")
        }
        assert "ix_claude_call_log_batch_result_lookup" in {
            index["name"] for index in inspector.get_indexes("claude_call_log")
        }
        assert "uq_anthropic_batch_result_receipt_identity" in {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "anthropic_batch_result_receipts"
            )
        }
        assert "uq_anthropic_batch_result_receipt_provider_message" in {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "anthropic_batch_result_receipts"
            )
        }
        assert {
            "ck_anthropic_batch_result_receipts_state",
            "ck_anthropic_batch_result_receipts_metered_call_log",
        } <= {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "anthropic_batch_result_receipts"
            )
        }
        stored_context = connection.execute(
            sa.text("SELECT context FROM anthropic_batch_jobs WHERE id = 7")
        ).scalar_one()
        assert json.loads(stored_context) == legacy_context

        connection.execute(
            sa.text(
                "INSERT INTO anthropic_batch_result_receipts "
                "(batch_job_id, custom_id, state, result_type, usage_event_id, "
                "call_log_id, provider_message_id) VALUES "
                "(7, 'cvparse-2', 'metered', 'succeeded', 11, 13, 'msg_1')"
            )
        )
        connection.commit()

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO anthropic_batch_result_receipts "
                    "(batch_job_id, custom_id, state, result_type) VALUES "
                    "(7, 'cvparse-2', 'skipped', 'errored')"
                )
            )
        connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO anthropic_batch_result_receipts "
                    "(batch_job_id, custom_id, state, result_type) VALUES "
                    "(7, 'cvparse-3', 'metered', 'succeeded')"
                )
            )
        connection.rollback()
        connection.execute(batch_jobs.insert(), {"id": 8, "context": {}})
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO anthropic_batch_result_receipts "
                    "(batch_job_id, custom_id, state, result_type, usage_event_id, "
                    "call_log_id, provider_message_id) VALUES "
                    "(8, 'cvparse-8', 'metered', 'succeeded', 11, 13, 'msg_1')"
                )
            )
        connection.rollback()
        with pytest.raises(RuntimeError, match="must not be deleted"):
            revision.downgrade()

    engine.dispose()


def test_revision_188_postgres_trigger_blocks_update_and_delete():
    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "188_normalize_anthropic_batch_receipts.py"
    ).read_text(encoding="utf-8")

    assert "BEFORE UPDATE OR DELETE ON anthropic_batch_result_receipts" in source
