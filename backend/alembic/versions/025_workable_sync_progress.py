"""Add workable_sync_progress for live sync counts during import.

Revision ID: 025_workable_sync_progress
Revises: 024_role_additional_requirements
Create Date: 2026-02-17

"""

from alembic import op
import sqlalchemy as sa


revision = "025_workable_sync_progress"
down_revision = "024_role_additional_requirements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("workable_sync_progress", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("workable_sync_progress")
