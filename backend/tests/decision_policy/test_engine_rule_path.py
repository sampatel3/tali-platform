"""rule_path traces are recruiter-readable."""

from __future__ import annotations

from app.decision_policy.engine import DecisionInputs, evaluate

from .conftest import bootstrap, make_org, make_role


def test_rule_path_records_each_step(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 80.0, "pre_screen_score": 70.0},
        flags={"must_have_blocked": False, "has_pending_assessment": False},
    )
    verdict = evaluate(inputs, db=db)
    # Should record: point:send_assessment, rule:skipped:must_have_blocked,
    # rule:skipped:has_pending_assessment, rule:fired:role_fit_score >= ...
    joined = " | ".join(verdict.rule_path)
    assert "point:send_assessment" in joined
    assert "rule:skipped" in joined
    assert "rule:fired" in joined


def test_reasoning_string_is_populated(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 80.0, "pre_screen_score": 70.0},
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.reasoning  # non-empty
    assert verdict.policy_revision_id is not None


def test_no_active_policy_returns_no_action(db):
    org = make_org(db)
    role = make_role(db, org=org)
    # Deliberately do NOT bootstrap.
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "no_action"
    assert "no_active_policy" in verdict.rule_path
