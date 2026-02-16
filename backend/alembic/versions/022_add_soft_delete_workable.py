"""Add deleted_at for soft-delete of Workable roles/candidates/applications.

Revision ID: 022_add_soft_delete_workable
Revises: 021_workable_sync_and_lemon_credits
Create Date: 2026-02-16

"""

from alembic import op
import sqlalchemy as sa


revision = "022_add_soft_delete_workable"
down_revision = "021_workable_sync_and_lemon_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("candidate_applications") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candidate_applications") as batch:
        batch.drop_column("deleted_at")
    with op.batch_alter_table("candidates") as batch:
        batch.drop_column("deleted_at")
    with op.batch_alter_table("roles") as batch:
        batch.drop_column("deleted_at")
