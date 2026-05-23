"""Add ``two_factor_required`` to ``organizations``.

The recruiter settings page already sent ``two_factor_required`` in the
org-update payload, but the column never existed so the value was
silently dropped and reverted on reload. Add the backing column (NOT
NULL, defaulting to false) so the toggle persists.

Revision ID: 098_add_org_two_factor_required
Revises: 097_add_decision_type_index
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "098_add_org_two_factor_required"
down_revision = "097_add_decision_type_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column(
                "two_factor_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("two_factor_required")
