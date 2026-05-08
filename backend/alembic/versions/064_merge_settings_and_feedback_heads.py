"""Merge concurrent heads (060_add_settings_redesign_fields + 063_add_decision_feedback).

Two parallel branches off ``059_add_share_links`` left the alembic graph
with two heads: the Settings redesign branch (060_add_settings_redesign_fields)
and the agentic-recruiting branch that ran 060_add_role_cohort_signals →
061 → 062 → 063_add_decision_feedback.

This merge migration unifies them so ``alembic upgrade head`` resolves to
a single revision. No schema changes — pure merge marker.

Revision ID: 064_merge_settings_and_feedback_heads
Revises: 060_add_settings_redesign_fields, 063_add_decision_feedback
Create Date: 2026-05-08
"""

from __future__ import annotations


revision = "064_merge_settings_and_feedback_heads"
down_revision = ("060_add_settings_redesign_fields", "063_add_decision_feedback")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
