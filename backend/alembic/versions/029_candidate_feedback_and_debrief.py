"""Add candidate feedback and interview debrief persistence fields.

Revision ID: 029_candidate_feedback_and_debrief
Revises: 028_workable_sync_runs
Create Date: 2026-02-22

"""

from alembic import op
import sqlalchemy as sa


revision = "029_candidate_feedback_and_debrief"
down_revision = "028_workable_sync_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("candidate_feedback_json", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("candidate_feedback_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessments", sa.Column("candidate_feedback_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "assessments",
        sa.Column(
            "candidate_feedback_ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "assessments",
        sa.Column(
            "candidate_feedback_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column("assessments", sa.Column("interview_debrief_json", sa.JSON(), nullable=True))
    op.add_column("assessments", sa.Column("interview_debrief_generated_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        "organizations",
        sa.Column(
            "candidate_feedback_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "candidate_feedback_enabled")

    op.drop_column("assessments", "interview_debrief_generated_at")
    op.drop_column("assessments", "interview_debrief_json")
    op.drop_column("assessments", "candidate_feedback_enabled")
    op.drop_column("assessments", "candidate_feedback_ready")
    op.drop_column("assessments", "candidate_feedback_sent_at")
    op.drop_column("assessments", "candidate_feedback_generated_at")
    op.drop_column("assessments", "candidate_feedback_json")

