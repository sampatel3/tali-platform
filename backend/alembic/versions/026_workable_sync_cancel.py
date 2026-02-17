"""Add workable_sync_cancel_requested_at to allow stopping a running sync.

Revision ID: 026_workable_sync_cancel
Revises: 025_workable_sync_progress
Create Date: 2026-02-17

"""

from alembic import op
import sqlalchemy as sa


revision = "026_workable_sync_cancel"
down_revision = "025_workable_sync_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("workable_sync_cancel_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("workable_sync_cancel_requested_at")
