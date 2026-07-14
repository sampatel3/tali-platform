"""Add durable role-agent bootstrap acknowledgement.

Revision ID: 159_add_agent_bootstrap_state
Revises: 158_drop_pipeline_stages_and_dispositions
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "159_add_agent_bootstrap_state"
down_revision = "158_drop_pipeline_stages_and_dispositions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles", sa.Column("agent_bootstrap_status", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "roles", sa.Column("agent_bootstrap_error", sa.Text(), nullable=True)
    )
    op.add_column(
        "roles",
        sa.Column("agent_bootstrap_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "roles",
        sa.Column("agent_bootstrap_completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("roles", "agent_bootstrap_completed_at")
    op.drop_column("roles", "agent_bootstrap_started_at")
    op.drop_column("roles", "agent_bootstrap_error")
    op.drop_column("roles", "agent_bootstrap_status")
