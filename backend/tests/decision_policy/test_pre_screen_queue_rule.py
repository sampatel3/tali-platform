"""Pre-screen-below-threshold queue rule.

Sibling to the existing ``test_pre_screen_auto_reject_rule.py``: that
test pins the legacy ``auto_reject`` short-circuit path (used by the
Celery auto-reject job when ``org.workable_config.auto_reject_enabled``
is on). This file pins the new agent-time path — when the recruiter has
NOT opted into auto-reject but pre-screen has flagged the candidate
below the role's score threshold, the engine should return a
``queue_skip_assessment_reject_decision`` verdict with
``reject_reason='pre_screen_below_threshold'`` so the agent surfaces it
to the Decision Hub instead of walking away.
"""

from __future__ import annotations

from app.decision_policy.engine import DecisionInputs, evaluate

from .conftest import bootstrap, make_org, make_role


def test_pre_screen_below_threshold_queues_skip_assessment_reject(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)

    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"pre_screen_score": 40.0},
        flags={
            # NOT eligible for legacy short-circuit (auto-reject org switch
            # is off), but the score IS below threshold.
            "pre_screen_auto_reject_eligible": False,
            "pre_screen_below_threshold": True,
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "queue_skip_assessment_reject_decision"
    assert verdict.decision_point == "reject"
    assert verdict.reject_reason == "pre_screen_below_threshold"


def test_pre_screen_below_threshold_bypasses_confidence_floor(db):
    """Reject point's confidence floor is 0.6 and weights are anchored on
    ``role_fit_score``. Pre-screen-stage candidates may have no role_fit
    score, but the new queue rule should still fire — it's a hard,
    deterministic threshold check, not a probabilistic verdict."""
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)

    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"pre_screen_score": 28.0},  # role_fit_score absent
        flags={
            "pre_screen_auto_reject_eligible": False,
            "pre_screen_below_threshold": True,
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "queue_skip_assessment_reject_decision"
    assert "confidence_floor_blocked" not in " ".join(verdict.rule_path)


def test_pending_assessment_blocks_pre_screen_queue_rule(db):
    """A candidate with an assessment in flight must not get cut at the
    pre-screen stage — the assessment outcome will decide."""
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)

    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"pre_screen_score": 30.0},
        flags={
            "pre_screen_auto_reject_eligible": False,
            "pre_screen_below_threshold": True,
            "no_pending_assessment": False,
            "has_pending_assessment": True,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type != "queue_skip_assessment_reject_decision"


def test_role_fit_low_carries_reject_reason(db):
    """The existing role_fit ≤ ceiling reject rule should now stamp
    ``reject_reason='role_fit_low'`` so the Hub can distinguish it from
    pre-screen rejects."""
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)

    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        # role_fit_score must be at or below the reject ceiling
        # (min(30, role_fit_min-25)). With score_threshold=65 → role_fit_min=65
        # → role_fit_max = min(30, 40) = 30.
        scores={"role_fit_score": 25.0},
        flags={
            "pre_screen_auto_reject_eligible": False,
            "pre_screen_below_threshold": False,
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "queue_reject_decision"
    assert verdict.reject_reason == "role_fit_low"
