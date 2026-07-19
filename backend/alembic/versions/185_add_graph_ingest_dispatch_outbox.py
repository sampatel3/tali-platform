"""Add durable listener-driven graph-ingest dispatch evidence.

Revision ID: 185_graph_ingest_dispatch
Revises: 184_assessment_result_delivery
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "185_graph_ingest_dispatch"
down_revision = "184_assessment_result_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_ingest_dispatches",
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("work_kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "dispatch_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("dispatch_nonce", sa.String(length=36), nullable=True),
        sa.Column("worker_attempt_nonce", sa.String(length=36), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "provider_attempt_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "work_kind IN ('candidate', 'interview', 'event')",
            name="ck_graph_ingest_dispatches_work_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatching', 'queued', 'claimed', "
            "'provider_started', 'complete', 'skipped', "
            "'reconciliation_required')",
            name="ck_graph_ingest_dispatches_status",
        ),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_graph_ingest_dispatches_organization_id",
        "graph_ingest_dispatches",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_ingest_dispatches_status",
        "graph_ingest_dispatches",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_graph_ingest_dispatches_recovery",
        "graph_ingest_dispatches",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_graph_ingest_dispatches_entity",
        "graph_ingest_dispatches",
        ["work_kind", "entity_id"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 185 is intentionally irreversible: graph-ingest dispatch "
        "rows are operational evidence and must not be deleted."
    )
