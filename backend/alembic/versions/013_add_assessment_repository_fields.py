"""Add assessment repository tracking fields

Revision ID: 013_repo_fields
Revises: 012_enterprise_access_controls
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa

revision = "013_repo_fields"
down_revision = "012_enterprise_access_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assessments", sa.Column("assessment_repo_url", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("assessment_branch", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("clone_command", sa.Text(), nullable=True))
    op.add_column("assessments", sa.Column("final_repo_state", sa.String(), nullable=True))
    op.add_column("assessments", sa.Column("git_evidence", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("assessments", "git_evidence")
    op.drop_column("assessments", "final_repo_state")
    op.drop_column("assessments", "clone_command")
    op.drop_column("assessments", "assessment_branch")
    op.drop_column("assessments", "assessment_repo_url")
