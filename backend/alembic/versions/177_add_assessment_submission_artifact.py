"""Add immutable assessment submission artifacts.

Revision ID: 177_assessment_submission_artifact
Revises: 176_graph_outbox_role_id
"""

from alembic import op
import sqlalchemy as sa


revision = "177_assessment_submission_artifact"
down_revision = "176_graph_outbox_role_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("submission_artifact", sa.JSON(), nullable=True))
    op.add_column(
        "assessments",
        sa.Column("submission_artifact_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "assessments",
        sa.Column("submission_artifact_captured_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessments", "submission_artifact_captured_at")
    op.drop_column("assessments", "submission_artifact_sha256")
    op.drop_column("assessments", "submission_artifact")
