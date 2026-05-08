"""Merge concurrent alembic heads: cohort-planner HITL + bucketed/decision merge.

Two PRs landed against ``066_add_decision_policies`` in parallel:

- ``067_add_agent_needs_input_and_send_assessment_hitl`` (PR #109,
  cohort planner) extended chain B (decision policies) with the
  agent-needs-input + send-assessment HITL columns.
- ``069_merge_bucketed_criteria_with_decision_policies`` (PR #107,
  chip-based role intent) added a merge marker pointing at the *old*
  chain-B tip ``066_add_decision_policies`` instead of #109's new tip.

The result: ``067_add_agent_needs_input_and_send_assessment_hitl`` and
``069_merge_bucketed_criteria_with_decision_policies`` are both heads.
``alembic upgrade head`` errors ("Multiple head revisions are present")
and the Railway start script fails fast on it, restart-looping the web
service. This is a pure merge marker — no schema changes.

Revision ID: 070_merge_cohort_planner_with_bucketed_decision_merge
Revises: 069_merge_bucketed_criteria_with_decision_policies, 067_add_agent_needs_input_and_send_assessment_hitl
Create Date: 2026-05-08
"""

from __future__ import annotations


revision = "070_merge_cohort_planner_with_bucketed_decision_merge"
down_revision = (
    "069_merge_bucketed_criteria_with_decision_policies",
    "067_add_agent_needs_input_and_send_assessment_hitl",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
