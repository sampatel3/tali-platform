"""Normalize Anthropic batch per-result metering receipts.

Revision ID: 188_anthropic_batch_receipts
Revises: 187_graph_ingest_manifest
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "188_anthropic_batch_receipts"
down_revision = "187_graph_ingest_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anthropic_batch_result_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_job_id", sa.Integer(), nullable=False),
        sa.Column("custom_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("result_type", sa.String(length=64), nullable=False),
        sa.Column("usage_event_id", sa.Integer(), nullable=True),
        sa.Column("call_log_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_message_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('metered', 'skipped')",
            name="ck_anthropic_batch_result_receipts_state",
        ),
        sa.CheckConstraint(
            "state = 'skipped' OR call_log_id IS NOT NULL",
            name="ck_anthropic_batch_result_receipts_metered_call_log",
        ),
        sa.ForeignKeyConstraint(
            ["batch_job_id"],
            ["anthropic_batch_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["call_log_id"],
            ["claude_call_log.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["usage_event_id"],
            ["usage_events.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_job_id",
            "custom_id",
            name="uq_anthropic_batch_result_receipt_identity",
        ),
        sa.UniqueConstraint(
            "provider_message_id",
            name="uq_anthropic_batch_result_receipt_provider_message",
        ),
    )
    op.create_index(
        "ix_claude_call_log_batch_result_lookup",
        "claude_call_log",
        ["anthropic_request_id", "feature_hint", "status"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE INDEX ix_usage_events_batch_id
            ON usage_events ((metadata ->> 'batch_id'))
            WHERE metadata IS NOT NULL
            """
        )
        op.execute(
            """
            CREATE FUNCTION prevent_anthropic_batch_receipt_mutation_v188()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'Anthropic batch result receipts are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_anthropic_batch_receipt_immutable
            BEFORE UPDATE OR DELETE ON anthropic_batch_result_receipts
            FOR EACH ROW
            EXECUTE FUNCTION prevent_anthropic_batch_receipt_mutation_v188()
            """
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 188 is intentionally irreversible: Anthropic batch result "
        "receipts are retained billing evidence and must not be deleted."
    )
