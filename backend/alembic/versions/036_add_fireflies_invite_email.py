"""Add org-level Fireflies invite email.

Revision ID: 036_add_fireflies_invite_email
Revises: 035_workable_first_hiring_intelligence
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "036_add_fireflies_invite_email"
down_revision = "035_workable_first_hiring_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("fireflies_invite_email", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("fireflies_invite_email")
