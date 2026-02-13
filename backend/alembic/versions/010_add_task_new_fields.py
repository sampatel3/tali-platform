"""Add new task fields: task_key, role, scenario, repo_structure, evaluation_rubric, extra_data

Revision ID: 010
Revises: 009
Create Date: 2026-02-13
"""
from alembic import op
import sqlalchemy as sa

revision = "010_add_task_new_fields"
down_revision = "009_fastapi_users"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("task_key", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("role", sa.String(), nullable=True))
    op.add_column("tasks", sa.Column("scenario", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("repo_structure", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("evaluation_rubric", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("extra_data", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_tasks_task_key"), "tasks", ["task_key"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_tasks_task_key"), table_name="tasks")
    op.drop_column("tasks", "extra_data")
    op.drop_column("tasks", "evaluation_rubric")
    op.drop_column("tasks", "repo_structure")
    op.drop_column("tasks", "scenario")
    op.drop_column("tasks", "role")
    op.drop_column("tasks", "task_key")
