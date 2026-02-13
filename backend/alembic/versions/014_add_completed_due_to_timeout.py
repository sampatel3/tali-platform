"""Add completed_due_to_timeout to assessments

Revision ID: 014_add_completed_due_to_timeout
Revises: 013_repo_fields
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa

revision = "014_add_completed_due_to_timeout"
down_revision = "013_repo_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "assessments",
        sa.Column("completed_due_to_timeout", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("assessments", "completed_due_to_timeout", server_default=None)


def downgrade():
    op.drop_column("assessments", "completed_due_to_timeout")
