"""Add per-task Claude budget limit field.

Revision ID: 016_add_task_claude_budget_limit
Revises: 015_role_first_applications_pause_reset
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa

revision = "016_add_task_claude_budget_limit"
down_revision = "015_role_first_applications_pause_reset"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("claude_budget_limit_usd", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("tasks", "claude_budget_limit_usd")
