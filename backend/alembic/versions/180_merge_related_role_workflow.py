"""Merge the audit and independent related-role migration histories.

Revision ID: 180_merge_related_role_workflow
Revises: 179_restore_schema_metadata_invariants, 174_related_role_workflow
Create Date: 2026-07-16
"""

from __future__ import annotations


revision = "180_merge_related_role_workflow"
down_revision = (
    "179_restore_schema_metadata_invariants",
    "174_related_role_workflow",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both already-applied schema branches without changing data."""


def downgrade() -> None:
    """Return to both parent heads without reversing either branch."""
