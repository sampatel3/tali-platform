"""Merge the published audit head with workspace bulk-role pause conversion.

Revision ID: 181_merge_workspace_bulk_role_pause
Revises: 180_merge_related_role_workflow, 175_workspace_bulk_role_pause
Create Date: 2026-07-16
"""

from __future__ import annotations


revision = "181_merge_workspace_bulk_role_pause"
down_revision = (
    "180_merge_related_role_workflow",
    "175_workspace_bulk_role_pause",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both already-applied branches without changing application data."""


def downgrade() -> None:
    """Return to both parent heads without reversing either branch."""
