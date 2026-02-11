"""add foreign key indexes for core tables

Revision ID: 002
Revises: 001
Create Date: 2026-02-11
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_assessments_organization_id", "assessments", ["organization_id"], unique=False)
    op.create_index("ix_assessments_candidate_id", "assessments", ["candidate_id"], unique=False)
    op.create_index("ix_assessments_task_id", "assessments", ["task_id"], unique=False)
    op.create_index("ix_users_organization_id", "users", ["organization_id"], unique=False)
    op.create_index("ix_tasks_organization_id", "tasks", ["organization_id"], unique=False)
    op.create_index("ix_candidates_organization_id", "candidates", ["organization_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_candidates_organization_id", table_name="candidates")
    op.drop_index("ix_tasks_organization_id", table_name="tasks")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_index("ix_assessments_task_id", table_name="assessments")
    op.drop_index("ix_assessments_candidate_id", table_name="assessments")
    op.drop_index("ix_assessments_organization_id", table_name="assessments")
