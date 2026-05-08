"""Hard rules (must_have_blocked, manual-action skip) short-circuit before scoring."""

from __future__ import annotations

from app.decision_policy.engine import DecisionInputs, ManualAction, evaluate

from .conftest import bootstrap, make_org, make_role


def test_must_have_blocked_auto_rejects_regardless_of_score(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 95.0, "pre_screen_score": 90.0},
        flags={"must_have_blocked": True, "has_pending_assessment": False},
    )
    verdict = evaluate(inputs, db=db)
    assert verdict.decision_type == "auto_reject"
    assert verdict.decision_point == "send_assessment"
    assert any("must_have_blocked" in step for step in verdict.rule_path)


def test_recent_recruiter_send_skips_send_assessment(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 95.0, "pre_screen_score": 90.0},
        flags={"must_have_blocked": False, "has_pending_assessment": False},
        manual_actions=[
            ManualAction(kind="sent_assessment", timestamp_iso="2026-05-08T10:00:00Z")
        ],
    )
    verdict = evaluate(inputs, db=db)
    # send_assessment was skipped; downstream points may produce no_action.
    assert verdict.decision_type in {"skip", "no_action"}
    if verdict.decision_type == "skip":
        assert verdict.skipped_due_to_manual is True


def test_recent_recruiter_reject_skips_all_three_points(db):
    org = make_org(db)
    role = make_role(db, org=org, score_threshold=65)
    bootstrap(db, org)
    inputs = DecisionInputs(
        application_id=1,
        role_id=int(role.id),
        organization_id=int(org.id),
        scores={"role_fit_score": 10.0, "pre_screen_score": 10.0},
        flags={"no_pending_assessment": True},
        manual_actions=[
            ManualAction(kind="rejected", timestamp_iso="2026-05-08T10:00:00Z")
        ],
    )
    verdict = evaluate(inputs, db=db)
    # All points should be skipped; the engine returns the last skip
    # (which is itself a "skip" or a "no_action" if no points existed).
    assert verdict.skipped_due_to_manual is True
    assert verdict.decision_type == "skip"
