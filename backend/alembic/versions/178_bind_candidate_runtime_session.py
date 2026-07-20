"""Bind each live candidate assessment to one runtime session.

Revision ID: 178_candidate_runtime_session
Revises: 177_assessment_submission_artifact
"""

from alembic import op
import sqlalchemy as sa


revision = "178_candidate_runtime_session"
down_revision = "177_assessment_submission_artifact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("candidate_session_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("candidate_session_bound_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessments", "candidate_session_bound_at")
    op.drop_column("assessments", "candidate_session_hash")
