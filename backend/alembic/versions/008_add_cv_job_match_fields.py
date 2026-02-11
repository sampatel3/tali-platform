"""add CV-job match scoring fields to assessments

Revision ID: 008_add_cv_job_match_fields
Revises: 007_add_candidate_document_fields
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa


revision = "008_add_cv_job_match_fields"
down_revision = "007_add_candidate_document_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("cv_job_match_score", sa.Float(), nullable=True))
    op.add_column("assessments", sa.Column("cv_job_match_details", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assessments", "cv_job_match_details")
    op.drop_column("assessments", "cv_job_match_score")
