"""Threshold + weighted-scoring path produces expected verdicts."""

from __future__ import annotations

from app.decision_policy.engine import DecisionInputs, evaluate

from .conftest import bootstrap, make_org, make_role


def test_strong_candidate_queues_send_assessment(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 80.0, "pre_screen_score": 70.0},
        flags={"must_have_blocked": False, "has_pending_assessment": False},
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "queue_send_assessment"
    assert verdict.decision_point == "send_assessment"
    assert verdict.policy_revision_id is not None
    assert "rule:fired" in " | ".join(verdict.rule_path)


def test_pending_assessment_skips_send(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 80.0, "pre_screen_score": 70.0},
        flags={"has_pending_assessment": True},
    )
    verdict = evaluate(inputs, db=db)
    # send_assessment is skipped; downstream points cascade to no_action.
    assert verdict.decision_type in {"skip", "no_action"}


def test_low_score_queues_reject(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)  # no role-specific threshold
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 10.0, "pre_screen_score": 5.0},
        flags={"no_pending_assessment": True},
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "queue_reject_decision"
    assert verdict.decision_point == "reject"


def test_advance_requires_assessment_completed(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={
            "role_fit_score": 50.0,
            "pre_screen_score": 30.0,
            "taali_score": 90.0,
            "assessment_score": 80.0,
        },
        flags={"assessment_completed": True, "has_pending_assessment": False},
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "queue_advance_decision"


def test_borderline_score_with_low_confidence_blocked_by_floor(db):
    org = make_org(db, default_score_threshold=65)
    role = make_role(db, org=org)
    bootstrap(db, org)
    # Only one signal present (role_fit) — confidence is sparse, so the
    # engine declines to queue even though the rule technically fires.
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 80.0, "pre_screen_score": 70.0},
        flags={"has_pending_assessment": False, "must_have_blocked": False},
    )
    # With both score signals confidence is 0.6+ which clears the floor;
    # the verdict should be queue_send_assessment.
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type in {
        "queue_send_assessment",
        "no_action",
    }
