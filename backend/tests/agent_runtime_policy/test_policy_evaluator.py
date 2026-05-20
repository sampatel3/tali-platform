"""policy_evaluator: end-to-end sub-agents → engine bridge."""

from __future__ import annotations

from app.agent_runtime.policy_evaluator import evaluate_for_application

from .conftest import add_event, make_world


def test_strong_candidate_yields_queue_send_assessment(db):
    org, role, _, app = make_world(db)
    # Cache a strong CV match + pre-screen so sub-agents return ok.
    app.cv_match_details = {
        "role_fit_score": 80.0,
        "dimension_scores": {},
        "requirements_assessment": [],
    }
    app.pre_screen_score_100 = 80.0
    db.flush()
    verdict, outputs = evaluate_for_application(
        db, role=role, application_id=int(app.id)
    )
    assert verdict.decision_type == "queue_send_assessment"
    assert verdict.policy_revision_id is not None
    assert "cv_scoring" in outputs
    assert "pre_screen" in outputs


def test_recent_recruiter_send_skips_send_assessment(db):
    org, role, _, app = make_world(db)
    app.cv_match_details = {"role_fit_score": 80.0, "dimension_scores": {}}
    app.pre_screen_score_100 = 80.0
    db.flush()
    add_event(
        db,
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        event_type="assessment_invite_sent",
    )
    verdict, _ = evaluate_for_application(
        db, role=role, application_id=int(app.id)
    )
    # send_assessment skipped; downstream points cascade to no_action.
    assert verdict.decision_type in {"skip", "no_action"}


def test_missing_application_returns_no_action(db):
    _org, role, _, _app = make_world(db)
    verdict, outputs = evaluate_for_application(
        db, role=role, application_id=999_999
    )
    assert verdict.decision_type == "no_action"
    assert outputs == {}


def test_pre_screen_below_threshold_queues_with_reject_reason(db):
    """End-to-end: a candidate scored well below the role's pre-screen
    threshold (with no role_fit signal) gets a queueable verdict with
    reject_reason='pre_screen_below_threshold'. Confirms the
    policy_evaluator computes the flag and the engine fires the new
    rule."""
    org, role, _, app = make_world(db)
    # Role threshold is 65 (from conftest). Score 30 is well below.
    role.score_threshold = 65
    app.pre_screen_score_100 = 30.0
    # Deliberately leave cv_match_details unset so role_fit_score
    # doesn't get computed — the new queue rule must fire without it.
    app.application_outcome = "open"
    db.flush()
    verdict, _ = evaluate_for_application(
        db, role=role, application_id=int(app.id)
    )
    assert verdict.decision_type == "queue_skip_assessment_reject_decision"
    assert verdict.reject_reason == "pre_screen_below_threshold"
