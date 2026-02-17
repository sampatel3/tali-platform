"""Add workable_sync_started_at for DB-backed sync-in-progress (multi-worker).

Revision ID: 023_workable_sync_started_at
Revises: 022_add_soft_delete_workable
Create Date: 2026-02-16

"""

from alembic import op
import sqlalchemy as sa


revision = "023_workable_sync_started_at"
down_revision = "022_add_soft_delete_workable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("workable_sync_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("workable_sync_started_at")
