"""Merge the role-collaboration and role-query-index migration branches.

Revision ID: 170_merge_role_migration_heads
Revises: 169_role_collaboration_controls, 169_role_page_query_indexes
Create Date: 2026-07-15
"""

from __future__ import annotations


revision = "170_merge_role_migration_heads"
down_revision = (
    "169_role_collaboration_controls",
    "169_role_page_query_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two already-applied schema histories."""


def downgrade() -> None:
    """Split the histories without reverting either parent migration."""
