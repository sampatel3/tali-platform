"""Create workable_sync_runs table for run-aware sync status/cancel.

Revision ID: 028_workable_sync_runs
Revises: 027_workable_candidate_profile_fields
Create Date: 2026-02-20

"""

from alembic import op
import sqlalchemy as sa


revision = "028_workable_sync_runs"
down_revision = "027_workable_candidate_profile_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workable_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False, server_default="metadata"),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(), nullable=True),
        sa.Column("jobs_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applications_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.Column("db_snapshot", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workable_sync_runs_organization_id", "workable_sync_runs", ["organization_id"])
    op.create_index("ix_workable_sync_runs_requested_by_user_id", "workable_sync_runs", ["requested_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_workable_sync_runs_requested_by_user_id", table_name="workable_sync_runs")
    op.drop_index("ix_workable_sync_runs_organization_id", table_name="workable_sync_runs")
    op.drop_table("workable_sync_runs")
