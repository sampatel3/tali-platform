"""Merge the agent-automation and candidate-search migration branches.

Revision ID: 164_merge_agent_search_heads
Revises: 160_add_candidate_search_indexes, 163_agent_policy_fields
Create Date: 2026-07-14
"""

from __future__ import annotations


revision = "164_merge_agent_search_heads"
down_revision = (
    "160_add_candidate_search_indexes",
    "163_agent_policy_fields",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two already-applied schema histories."""


def downgrade() -> None:
    """Split the histories without reverting either parent migration."""
