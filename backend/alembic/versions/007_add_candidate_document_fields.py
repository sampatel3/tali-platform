"""add CV and job spec document fields to candidates

Revision ID: 007_add_candidate_document_fields
Revises: 006_add_email_verification
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa


revision = "007_add_candidate_document_fields"
down_revision = "006_add_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CV fields on candidates
    op.add_column("candidates", sa.Column("cv_file_url", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("cv_filename", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("cv_text", sa.Text(), nullable=True))
    op.add_column("candidates", sa.Column("cv_uploaded_at", sa.DateTime(timezone=True), nullable=True))

    # Job spec fields on candidates
    op.add_column("candidates", sa.Column("job_spec_file_url", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("job_spec_filename", sa.String(), nullable=True))
    op.add_column("candidates", sa.Column("job_spec_text", sa.Text(), nullable=True))
    op.add_column("candidates", sa.Column("job_spec_uploaded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "job_spec_uploaded_at")
    op.drop_column("candidates", "job_spec_text")
    op.drop_column("candidates", "job_spec_filename")
    op.drop_column("candidates", "job_spec_file_url")
    op.drop_column("candidates", "cv_uploaded_at")
    op.drop_column("candidates", "cv_text")
    op.drop_column("candidates", "cv_filename")
    op.drop_column("candidates", "cv_file_url")
