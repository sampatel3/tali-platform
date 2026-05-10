"""Inject pre-screen auto-reject rule into existing decision_policies rows.

The bootstrap default added in series 072 includes a new rule on the
``reject`` decision point that maps the
``pre_screen_auto_reject_eligible`` flag to the ``auto_reject`` action.
This data migration injects the same rule into every active
``decision_policies`` row so the Celery auto-reject path's verdict
flows through the decision engine for orgs and roles that were
bootstrapped before the rule existed — instead of relying on the
``evaluate_auto_reject_decision`` legacy threshold-fallback.

Trade-offs:
  * Mutates ``policy_json`` in place rather than creating new
    revisions. Past ``agent_decisions.policy_revision_id`` rows
    continue to reference these revision_ids, which now describe
    slightly-augmented content. Acceptable because the added rule is
    behaviour-equivalent to the legacy threshold check the orgs were
    getting before — no past decision was produced via the engine for
    this rule's condition.
  * Idempotent: a policy that already contains a rule keyed on
    ``pre_screen_auto_reject_eligible`` is left alone.

Revision ID: 073_inject_pre_screen_auto_reject_rule
Revises: 072_replace_send_assessment_hitl_with_two_toggles
Create Date: 2026-05-10
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "073_inject_pre_screen_auto_reject_rule"
down_revision = "072_replace_send_assessment_hitl_with_two_toggles"
branch_labels = None
depends_on = None


_RULE = {
    "if": "pre_screen_auto_reject_eligible",
    "then": "auto_reject",
    "priority": 70,
    "reason_template": (
        "Pre-screen score below the configured threshold; "
        "auto-rejecting at pre-screen stage."
    ),
}


def _coerce_policy(raw):
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, policy_json FROM decision_policies "
            "WHERE deactivated_at IS NULL"
        )
    ).fetchall()

    for row in rows:
        pid = row[0]
        policy = _coerce_policy(row[1])
        if not isinstance(policy, dict):
            continue

        decision_points = policy.setdefault("decision_points", {})
        if not isinstance(decision_points, dict):
            continue
        reject = decision_points.setdefault(
            "reject",
            {
                "thresholds": {},
                "weights": {"role_fit_score": 1.0},
                "rules": [],
                "confidence_floor": 0.6,
            },
        )
        if not isinstance(reject, dict):
            continue
        rules = reject.setdefault("rules", [])
        if not isinstance(rules, list):
            continue

        # Idempotency: skip when the rule is already present.
        if any(
            isinstance(r, dict) and r.get("if") == _RULE["if"] for r in rules
        ):
            continue

        rules.append(dict(_RULE))
        # Keep canonical ordering (highest priority first) so the engine's
        # priority-descending walk doesn't depend on row insertion order.
        rules.sort(key=lambda r: -int(r.get("priority", 0)) if isinstance(r, dict) else 0)

        bind.execute(
            sa.text(
                "UPDATE decision_policies SET policy_json = :pj WHERE id = :id"
            ),
            {"pj": json.dumps(policy), "id": pid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, policy_json FROM decision_policies")
    ).fetchall()

    for row in rows:
        pid = row[0]
        policy = _coerce_policy(row[1])
        if not isinstance(policy, dict):
            continue

        reject = (policy.get("decision_points") or {}).get("reject") or {}
        rules = reject.get("rules") if isinstance(reject, dict) else None
        if not isinstance(rules, list):
            continue

        new_rules = [
            r
            for r in rules
            if not (isinstance(r, dict) and r.get("if") == _RULE["if"])
        ]
        if len(new_rules) == len(rules):
            continue

        reject["rules"] = new_rules
        bind.execute(
            sa.text(
                "UPDATE decision_policies SET policy_json = :pj WHERE id = :id"
            ),
            {"pj": json.dumps(policy), "id": pid},
        )
