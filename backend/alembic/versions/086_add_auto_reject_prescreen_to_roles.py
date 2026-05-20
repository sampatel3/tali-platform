"""Add per-role auto_reject_prescreen toggle + thread reject_reason into
existing policies.

Splits "auto-reject the agent's reject decisions" into two flags:

- ``role.auto_reject`` (existing): master — when True, any reject the
  agent queues executes immediately without HITL approval.
- ``role.auto_reject_prescreen`` (new): granular — when True, only
  rejects whose policy verdict cited the pre-screen-below-threshold
  rule auto-execute. Recruiters who want bulk pre-screen culls without
  giving up HITL on judgment-based rejects flip this one.

Default ``false`` keeps every reject in the HITL queue until the
recruiter explicitly opts in.

Also patches existing ``decision_policies.policy_json`` blobs so the
new ``reject`` rule (``pre_screen_below_threshold``) is present, and
the existing reject rules carry ``reject_reason`` tags. Idempotent —
skips rules that already match. Pre-pilot, no recruiter has tuned a
policy by hand, so this is safe.

Revision ID: 086_add_auto_reject_prescreen_to_roles
Revises: 085_rename_technical_interview_to_advanced
Create Date: 2026-05-20
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "086_add_auto_reject_prescreen_to_roles"
down_revision = "085_rename_technical_interview_to_advanced"
branch_labels = None
depends_on = None


_PRESCREEN_RULE = {
    "if": "pre_screen_below_threshold AND no_pending_assessment",
    "then": "queue_skip_assessment_reject_decision",
    "priority": 60,
    "reason_template": (
        "Pre-screen score below the role's reject threshold with no "
        "assessment in flight — queueing reject without sending assessment."
    ),
    "reject_reason": "pre_screen_below_threshold",
}


def _patch_policy(policy_json: dict) -> tuple[dict, bool]:
    """Return (patched, changed). Idempotent."""
    if not isinstance(policy_json, dict):
        return policy_json, False
    changed = False
    decision_points = policy_json.get("decision_points") or {}
    reject_point = decision_points.get("reject")
    if not isinstance(reject_point, dict):
        return policy_json, False
    rules = list(reject_point.get("rules") or [])
    # Tag existing rules with reject_reason if missing.
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("reject_reason"):
            continue
        if rule.get("if") == "pre_screen_auto_reject_eligible":
            rule["reject_reason"] = "pre_screen_below_threshold"
            changed = True
        elif "role_fit_score" in str(rule.get("if") or ""):
            rule["reject_reason"] = "role_fit_low"
            changed = True
    # Insert the new prescreen-queue rule if not already there.
    already_present = any(
        isinstance(r, dict) and r.get("if") == _PRESCREEN_RULE["if"]
        for r in rules
    )
    if not already_present:
        rules.append(dict(_PRESCREEN_RULE))
        changed = True
    if changed:
        reject_point["rules"] = rules
        decision_points["reject"] = reject_point
        policy_json["decision_points"] = decision_points
    return policy_json, changed


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "auto_reject_prescreen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, policy_json FROM decision_policies "
                "WHERE deactivated_at IS NULL"
            )
        )
    )
    for row in rows:
        try:
            current = row.policy_json if isinstance(row.policy_json, dict) else json.loads(row.policy_json or "{}")
        except Exception:
            continue
        patched, changed = _patch_policy(current)
        if not changed:
            continue
        connection.execute(
            sa.text(
                "UPDATE decision_policies SET policy_json = :pj WHERE id = :id"
            ),
            {"pj": json.dumps(patched), "id": int(row.id)},
        )


def downgrade() -> None:
    op.drop_column("roles", "auto_reject_prescreen")
    # We intentionally do NOT strip the new rule + reject_reason tags
    # from policy_json on downgrade: leaving them is forward-compatible
    # with re-upgrade and the engine ignores reject_reason on an old
    # build (it's an additive PolicyDecision field).
