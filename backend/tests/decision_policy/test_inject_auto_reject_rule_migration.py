"""Migration 073 injects the pre-screen auto-reject rule into existing policies.

Tests run the migration's upgrade/downgrade helpers directly against a
populated test database; we don't spin up Alembic since the test
session uses SQLite.
"""

from __future__ import annotations

import json

from app.decision_policy.bootstrap import bootstrap_org
from app.models.decision_policy import DecisionPolicy

from .conftest import make_org


_RULE_IF = "pre_screen_auto_reject_eligible"


def _get_reject_rules(policy: DecisionPolicy) -> list[dict]:
    raw = policy.policy_json
    if isinstance(raw, str):
        raw = json.loads(raw)
    reject = (raw.get("decision_points") or {}).get("reject") or {}
    return list(reject.get("rules") or [])


def _strip_new_rule(policy: DecisionPolicy) -> None:
    """Mutate a freshly-bootstrapped policy_json to drop the new rule —
    simulating an org that bootstrapped before migration 073 ran."""
    raw = policy.policy_json
    if isinstance(raw, str):
        raw = json.loads(raw)
    reject = raw["decision_points"]["reject"]
    reject["rules"] = [
        r for r in reject["rules"] if r.get("if") != _RULE_IF
    ]
    policy.policy_json = raw


def _run_upgrade(db) -> None:
    """Inline reproduction of the migration's upgrade body — keeps the
    test self-contained without invoking Alembic against SQLite."""
    rows = list(
        db.query(DecisionPolicy)
        .filter(DecisionPolicy.deactivated_at.is_(None))
        .all()
    )
    for policy in rows:
        raw = policy.policy_json
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            continue
        decision_points = raw.setdefault("decision_points", {})
        reject = decision_points.setdefault(
            "reject",
            {
                "thresholds": {},
                "weights": {"role_fit_score": 1.0},
                "rules": [],
                "confidence_floor": 0.6,
            },
        )
        rules = reject.setdefault("rules", [])
        if any(r.get("if") == _RULE_IF for r in rules if isinstance(r, dict)):
            continue
        rules.append(
            {
                "if": _RULE_IF,
                "then": "auto_reject",
                "priority": 70,
                "reason_template": (
                    "Pre-screen score below the configured threshold; "
                    "auto-rejecting at pre-screen stage."
                ),
            }
        )
        rules.sort(key=lambda r: -int(r.get("priority", 0)))
        policy.policy_json = raw
    db.flush()


def _run_downgrade(db) -> None:
    rows = list(db.query(DecisionPolicy).all())
    for policy in rows:
        raw = policy.policy_json
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            continue
        reject = (raw.get("decision_points") or {}).get("reject") or {}
        rules = reject.get("rules") if isinstance(reject, dict) else None
        if not isinstance(rules, list):
            continue
        new_rules = [
            r for r in rules if not (isinstance(r, dict) and r.get("if") == _RULE_IF)
        ]
        if len(new_rules) == len(rules):
            continue
        reject["rules"] = new_rules
        policy.policy_json = raw
    db.flush()


def test_upgrade_injects_rule_into_legacy_policy(db):
    org = make_org(db)
    policy = bootstrap_org(db, organization_id=int(org.id))
    _strip_new_rule(policy)
    db.flush()
    assert all(r.get("if") != _RULE_IF for r in _get_reject_rules(policy))

    _run_upgrade(db)

    rules = _get_reject_rules(policy)
    matches = [r for r in rules if r.get("if") == _RULE_IF]
    assert len(matches) == 1
    assert matches[0]["then"] == "auto_reject"
    assert matches[0]["priority"] == 70


def test_upgrade_is_idempotent(db):
    org = make_org(db)
    policy = bootstrap_org(db, organization_id=int(org.id))
    # Bootstrap already wrote the rule; running upgrade should not duplicate.
    _run_upgrade(db)
    rules = _get_reject_rules(policy)
    matches = [r for r in rules if r.get("if") == _RULE_IF]
    assert len(matches) == 1


def test_upgrade_preserves_other_rules(db):
    org = make_org(db)
    policy = bootstrap_org(db, organization_id=int(org.id))
    _strip_new_rule(policy)
    db.flush()
    other_rules_before = [
        r for r in _get_reject_rules(policy) if r.get("if") != _RULE_IF
    ]

    _run_upgrade(db)

    other_rules_after = [
        r for r in _get_reject_rules(policy) if r.get("if") != _RULE_IF
    ]
    assert other_rules_before == other_rules_after


def test_downgrade_removes_only_the_injected_rule(db):
    org = make_org(db)
    policy = bootstrap_org(db, organization_id=int(org.id))
    rules_before = _get_reject_rules(policy)
    assert any(r.get("if") == _RULE_IF for r in rules_before)
    other_rules = [r for r in rules_before if r.get("if") != _RULE_IF]

    _run_downgrade(db)

    rules_after = _get_reject_rules(policy)
    assert all(r.get("if") != _RULE_IF for r in rules_after)
    assert rules_after == other_rules
