"""anthropic_batch_jobs — metering anchor for the Message Batches API.

A batch submission and its results retrieval happen in different
processes at different times. This table carries the attribution
(feature, org, per-custom_id entity map) from submit to retrieve so
every batch result writes claude_call_log + usage_events at the batch
tier (50% of standard). ``metered_at`` is the idempotency latch that
keeps repeated results() calls from double-billing.

Revision ID: 135_add_anthropic_batch_jobs
Revises: 134_add_role_auto_reject_pre_screen
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "135_add_anthropic_batch_jobs"
down_revision = "134_add_role_auto_reject_pre_screen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anthropic_batch_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(), nullable=False, server_default="submitted"
        ),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("metered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metered_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_anthropic_batch_jobs_batch_id",
        "anthropic_batch_jobs",
        ["batch_id"],
        unique=True,
    )
    op.create_index(
        "ix_anthropic_batch_jobs_status",
        "anthropic_batch_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_anthropic_batch_jobs_status", table_name="anthropic_batch_jobs")
    op.drop_index(
        "ix_anthropic_batch_jobs_batch_id", table_name="anthropic_batch_jobs"
    )
    op.drop_table("anthropic_batch_jobs")
