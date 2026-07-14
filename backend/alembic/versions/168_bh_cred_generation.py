"""Add an atomic Bullhorn credential-lineage fence.

Revision ID: 168_bh_cred_generation
Revises: 167_sister_eval_recovery
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "168_bh_cred_generation"
down_revision = "167_sister_eval_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "bullhorn_credential_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "bullhorn_credential_generation")
