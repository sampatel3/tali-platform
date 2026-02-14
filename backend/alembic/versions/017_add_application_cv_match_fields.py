"""Add CV match scoring fields to candidate applications.

Revision ID: 017_add_application_cv_match_fields
Revises: 016_add_task_claude_budget_limit
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa


revision = "017_add_application_cv_match_fields"
down_revision = "016_add_task_claude_budget_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_applications", sa.Column("cv_match_score", sa.Float(), nullable=True))
    op.add_column("candidate_applications", sa.Column("cv_match_details", sa.JSON(), nullable=True))
    op.add_column("candidate_applications", sa.Column("cv_match_scored_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_applications", "cv_match_scored_at")
    op.drop_column("candidate_applications", "cv_match_details")
    op.drop_column("candidate_applications", "cv_match_score")
