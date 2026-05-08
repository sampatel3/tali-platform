"""Merge concurrent heads (063_chat_role_scope_merge_heads + 064_merge_settings_and_feedback_heads).

Two PRs landed on main with overlapping migration ancestry:

- ``063_chat_role_scope_merge_heads`` (PR adding role-scoped Taali Chat)
  merged the 060_settings_redesign + 062 heads and added
  ``role_id`` to ``taali_chat_conversations``.
- ``064_merge_settings_and_feedback_heads`` (PR #96, the Hub) added
  062 → 063_add_decision_feedback → 064 merging 060_settings_redesign +
  063_add_decision_feedback.

Both descend from ``060_add_settings_redesign_fields`` but the two new
063s diverge from ``062_add_role_agent_next_run_at`` along separate
paths, leaving alembic with two heads. ``alembic upgrade head`` errors
("Multiple head revisions are present") and the Railway start script
fails fast on it, restart-looping the web service.

This migration unifies them. No schema changes — pure merge marker.

Revision ID: 065_merge_chat_scope_and_hub_feedback
Revises: 063_chat_role_scope_merge_heads, 064_merge_settings_and_feedback_heads
Create Date: 2026-05-08
"""

from __future__ import annotations


revision = "065_merge_chat_scope_and_hub_feedback"
down_revision = (
    "063_chat_role_scope_merge_heads",
    "064_merge_settings_and_feedback_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
