"""Merge collaboration controls and role-page index heads.

Revision ID: 170_merge_role_controls_page_indexes
Revises: 169_role_collaboration_controls, 169_role_page_query_indexes
Create Date: 2026-07-15
"""

from __future__ import annotations


revision = "170_merge_role_controls_page_indexes"
down_revision = (
    "169_role_collaboration_controls",
    "169_role_page_query_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
