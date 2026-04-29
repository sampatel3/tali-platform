"""Add background_job_runs to record live + recent runs of in-memory job kinds.

Used by the Settings → Background jobs panel to render history for scoring
batch, CV fetch, and graph sync (Workable sync already has its own table).

Revision ID: 052_add_background_job_runs
Revises: 051_add_pre_screen_run_at
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "052_add_background_job_runs"
down_revision = "051_add_pre_screen_run_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_job_runs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("scope_kind", sa.String, nullable=False),
        sa.Column("scope_id", sa.Integer, nullable=False),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("counters", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_background_job_runs_org_started",
        "background_job_runs",
        ["organization_id", "started_at"],
    )
    op.create_index(
        "ix_background_job_runs_kind_scope_started",
        "background_job_runs",
        ["kind", "scope_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_background_job_runs_kind_scope_started", table_name="background_job_runs")
    op.drop_index("ix_background_job_runs_org_started", table_name="background_job_runs")
    op.drop_table("background_job_runs")
