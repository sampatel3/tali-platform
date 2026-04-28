"""Add starred_for_auto_sync flag to roles.

Per-org boolean: when true, the role auto-syncs from Workable on a 15-min
Beat cadence and newly ingested candidates auto-enqueue CV scoring (gated
by the existing two-tier pre-screen).

Revision ID: 049_add_role_starred_for_auto_sync
Revises: 047_add_cv_embeddings
Create Date: 2026-04-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "049_add_role_starred_for_auto_sync"
down_revision = "047_add_cv_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "starred_for_auto_sync",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_roles_starred_for_auto_sync",
        "roles",
        ["starred_for_auto_sync"],
    )


def downgrade() -> None:
    op.drop_index("ix_roles_starred_for_auto_sync", table_name="roles")
    op.drop_column("roles", "starred_for_auto_sync")
