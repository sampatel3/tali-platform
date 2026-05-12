"""``graph_writeback_queue`` — Phase 6 of the multi-agent upgrade.

Medium-sensitivity graph hints (SIMILAR_TO, HIGH_YIELD, etc.) sit here
pending co-sign. Low-risk hints auto-commit (no row needed); high-risk
hints are blocked at validation time (no row written, blocklist entry
in audit log).

Revision ID: 080_add_graph_writeback_queue
Revises: 079_add_promotion_gate_tables
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "080_add_graph_writeback_queue"
down_revision = "079_add_promotion_gate_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_writeback_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_feedback_id",
            sa.BigInteger(),
            sa.ForeignKey("decision_feedback.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("hint_json", sa.JSON(), nullable=False),
        sa.Column("sensitivity", sa.String(length=8), nullable=False),  # low | medium | high
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending_cosign",
        ),  # pending_cosign | committed | rejected | blocked
        # Cosign metadata
        sa.Column("proposed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cosigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("cosigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cosign_note", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        # When the hint was actually applied to the graph (committed only).
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        # Graphiti episode that captures the feedback narrative — used
        # as the audit-trail anchor for the writeback.
        sa.Column("feedback_episode_uuid", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_graph_writeback_queue_active",
        "graph_writeback_queue",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_writeback_queue_active", table_name="graph_writeback_queue")
    op.drop_table("graph_writeback_queue")
