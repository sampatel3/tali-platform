"""Add interview focus fields to roles.

Revision ID: 019_add_role_interview_focus_fields
Revises: 018_add_demo_lead_fields
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa


revision = "019_add_role_interview_focus_fields"
down_revision = "018_add_demo_lead_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("interview_focus", sa.JSON(), nullable=True))
    op.add_column("roles", sa.Column("interview_focus_generated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("roles", "interview_focus_generated_at")
    op.drop_column("roles", "interview_focus")
