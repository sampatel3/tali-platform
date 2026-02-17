"""Add role additional_requirements for CV scoring criteria.

Revision ID: 024_role_additional_requirements
Revises: 023_workable_sync_started_at
Create Date: 2026-02-17

"""

from alembic import op
import sqlalchemy as sa


revision = "024_role_additional_requirements"
down_revision = "023_workable_sync_started_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.add_column(sa.Column("additional_requirements", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("roles") as batch:
        batch.drop_column("additional_requirements")
