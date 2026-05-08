"""Merge concurrent alembic heads: chip-based criteria chain + decision-policy.

Two PRs landed against ``065_merge_chat_scope_and_hub_feedback`` in
parallel:

- ``066_add_bucketed_criteria`` → ``067_drop_legacy_org_intent_columns``
  → ``068_drop_role_additional_requirements`` (chip composer
  feature — workspace + role criteria as structured chips)
- ``066_add_decision_policies`` (deterministic decision-policy
  surface for the orchestrator agent)

After both ship, alembic has two heads. ``alembic upgrade head`` errors
("Multiple head revisions are present") and the Railway start script
fails fast on it, restart-looping the web service. This is a pure merge
marker — no schema changes.

Revision ID: 069_merge_bucketed_criteria_with_decision_policies
Revises: 068_drop_role_additional_requirements, 066_add_decision_policies
Create Date: 2026-05-08
"""

from __future__ import annotations


revision = "069_merge_bucketed_criteria_with_decision_policies"
down_revision = (
    "068_drop_role_additional_requirements",
    "066_add_decision_policies",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
