"""Add manual_evaluation JSON to assessments for rubric-based evaluator UI

Revision ID: 011
Revises: 010_add_task_new_fields
Create Date: 2026-02-13

"""
from alembic import op
import sqlalchemy as sa

revision = "011_add_manual_evaluation"
down_revision = "010_add_task_new_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assessments", sa.Column("manual_evaluation", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("assessments", "manual_evaluation")
