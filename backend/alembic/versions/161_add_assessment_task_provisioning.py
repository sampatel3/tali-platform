"""Add durable assessment-task provisioning state to roles.

Revision ID: 161_task_provisioning_state
Revises: 160_backfill_requisition_criteria
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "161_task_provisioning_state"
down_revision = "160_backfill_requisition_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("assessment_task_provisioning", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("roles", "assessment_task_provisioning")
