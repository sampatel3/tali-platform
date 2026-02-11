"""add cv fields to assessments

Revision ID: 004_add_cv_fields_to_assessments
Revises: 003
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "004_add_cv_fields_to_assessments"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("cv_file_url", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("cv_filename", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("cv_uploaded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("assessments", "cv_uploaded_at")
    op.drop_column("assessments", "cv_filename")
    op.drop_column("assessments", "cv_file_url")
