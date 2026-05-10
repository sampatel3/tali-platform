"""Pre-screen-stage auto-reject is produced by the unified engine.

The Celery auto-reject path computes a per-role eligibility flag
(``pre_screen_auto_reject_eligible``) and the bootstrap default
``reject`` decision point has a rule that maps that flag to
``auto_reject``. This test pins both halves: the rule lives in the
default policy, and the engine fires it correctly when the flag is set.
"""

from __future__ import annotations

from app.decision_policy.engine import DecisionInputs, evaluate

from .conftest import bootstrap, make_org, make_role


def test_eligibility_flag_produces_auto_reject_verdict(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    # Pre-screen-stage candidate: role_fit_score is absent (Stage 2
    # hasn't run), but the eligibility flag is True. The auto_reject
    # rule should fire even without role_fit_score signal density.
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"pre_screen_score": 35.0},
        flags={
            "pre_screen_auto_reject_eligible": True,
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "auto_reject"
    assert verdict.decision_point == "reject"
    assert any(
        "pre_screen_auto_reject_eligible" in step for step in verdict.rule_path
    )


def test_eligibility_flag_false_does_not_auto_reject(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    # Same inputs but eligibility flag is False — the rule should not
    # fire and the engine should fall through to no_action (or the
    # legacy queue_reject_decision rule when role_fit signal is
    # present).
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"pre_screen_score": 35.0},
        flags={
            "pre_screen_auto_reject_eligible": False,
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type != "auto_reject"


def test_auto_reject_bypasses_confidence_floor(db):
    """`auto_reject` is a hard rule — fires even when other signals are absent.

    The reject point's confidence floor is 0.6 and weights are anchored
    to ``role_fit_score``. Pre-screen-stage candidates have no
    role_fit_score so signal-density confidence is 0.0, which would
    block any queueing action. ``auto_reject`` deliberately ignores the
    floor: the rule expressed certainty already.
    """
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"pre_screen_score": 12.0},  # no role_fit_score at all
        flags={
            "pre_screen_auto_reject_eligible": True,
            "no_pending_assessment": True,
            "has_pending_assessment": False,
            "must_have_blocked": False,
        },
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "auto_reject"
    assert "confidence_floor_blocked" not in " ".join(verdict.rule_path)
