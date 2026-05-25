"""Add ``graph_episode_outbox`` — durable queue for Graphiti episode writes.

Realised hiring outcomes (interviewed / hired / rejected_confirmed after an
approved agent decision) used to be emitted to Graphiti fire-and-forget: the
``emit_*`` helpers swallow every error, so a Graphiti outage silently dropped
the one training signal we can't reconstruct later. This table is the durable
hop — producers write a row in the same transaction as the calibration write,
and a Celery drain task ships pending rows to Graphiti with retry/backoff.

Revision ID: 104_add_graph_episode_outbox
Revises: 103_add_workable_stages_cache
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "104_add_graph_episode_outbox"
down_revision = "103_add_workable_stages_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_episode_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("episode_kind", sa.String(length=32), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_graph_episode_outbox_organization_id",
        "graph_episode_outbox",
        ["organization_id"],
    )
    op.create_index(
        "ix_graph_episode_outbox_dedup_key",
        "graph_episode_outbox",
        ["dedup_key"],
        unique=True,
    )
    # The drain task polls for pending rows; index status so it never table-scans.
    op.create_index(
        "ix_graph_episode_outbox_status",
        "graph_episode_outbox",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_episode_outbox_status", table_name="graph_episode_outbox")
    op.drop_index(
        "ix_graph_episode_outbox_dedup_key", table_name="graph_episode_outbox"
    )
    op.drop_index(
        "ix_graph_episode_outbox_organization_id", table_name="graph_episode_outbox"
    )
    op.drop_table("graph_episode_outbox")
