"""Talent-pool rediscovery Phase B: pool_rescore_jobs.

Stores opt-in re-scores of a shortlist against a NEW ad-hoc requirement, kept
separate from ``candidate_applications.cv_match_details`` (the canonical role
score). SQLite (tests, via create_all) builds this from the model; this migration
is the Postgres path.

Revision ID: 121_add_pool_rescore_jobs
Revises: 120_add_completed_due_to_timeout_status
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "121_add_pool_rescore_jobs"
down_revision = "120_add_completed_due_to_timeout_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pool_rescore_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("requirement_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("application_ids", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pool_rescore_jobs_organization_id", "pool_rescore_jobs", ["organization_id"]
    )
    op.create_index(
        "ix_pool_rescore_jobs_requirement_hash",
        "pool_rescore_jobs",
        ["requirement_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pool_rescore_jobs_requirement_hash", table_name="pool_rescore_jobs"
    )
    op.drop_index(
        "ix_pool_rescore_jobs_organization_id", table_name="pool_rescore_jobs"
    )
    op.drop_table("pool_rescore_jobs")
