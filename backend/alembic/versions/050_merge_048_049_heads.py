"""Merge concurrent migration heads 048 and 049.

Both 048_add_graph_sync_state and 049_add_role_starred_for_auto_sync were
applied as independent heads due to a deploy race. This empty merge migration
gives alembic a single head to upgrade to.

Revision ID: 050_merge_048_049_heads
Revises: 048_add_graph_sync_state, 049_add_role_starred_for_auto_sync
Create Date: 2026-04-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "050_merge_048_049_heads"
down_revision = ("048_add_graph_sync_state", "049_add_role_starred_for_auto_sync")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
