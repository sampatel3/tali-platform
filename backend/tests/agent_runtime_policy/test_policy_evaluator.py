"""policy_evaluator: end-to-end sub-agents → engine bridge."""

from __future__ import annotations

from app.agent_runtime.policy_evaluator import evaluate_for_application
from app.models.assessment import Assessment, AssessmentStatus
from app.models.task import Task

from .conftest import add_event, make_world


def _cache_genuine_prescreen(app, score: float, *, decision: str = "yes") -> None:
    """Mirror the canonical Stage-1 persistence contract without an LLM call."""

    app.pre_screen_score_100 = score
    app.genuine_pre_screen_score_100 = score
    app.pre_screen_evidence = {"decision": decision, "llm_score_100": score}


def test_strong_candidate_yields_queue_send_assessment(db):
    org, role, _, app = make_world(db)
    # Cache a strong CV match + pre-screen so sub-agents return ok.
    app.cv_match_details = {
        "role_fit_score": 80.0,
        "dimension_scores": {},
        "requirements_assessment": [],
    }
    _cache_genuine_prescreen(app, 80.0)
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
    _cache_genuine_prescreen(app, 80.0)
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


def test_explicit_must_have_failure_hits_deterministic_reject_rule(db):
    _org, role, _, app = make_world(db)
    app.cv_match_details = {
        "role_fit_score": 90.0,
        "dimension_scores": {},
        "requirements_assessment": [
            {
                "requirement_id": "crit_1",
                "priority": "must_have",
                "status": "missing",
                "blocker": True,
            }
        ],
    }
    _cache_genuine_prescreen(app, 90.0)
    db.flush()
    verdict, _ = evaluate_for_application(db, role=role, application_id=int(app.id))
    assert verdict.decision_type == "auto_reject"
    assert any("must_have_blocked" in step for step in verdict.rule_path)


def test_missing_application_returns_no_action(db):
    _org, role, _, _app = make_world(db)
    verdict, outputs = evaluate_for_application(
        db, role=role, application_id=999_999
    )
    assert verdict.decision_type == "no_action"
    assert outputs == {}


def test_incomplete_assessment_grading_short_circuits_before_subagents(db):
    org, role, candidate, app = make_world(db)
    task = Task(
        organization_id=org.id,
        name="Policy retry task",
        evaluation_rubric={"quality": {"weight": 1.0}},
    )
    db.add(task)
    db.flush()
    db.add(
        Assessment(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=role.id,
            application_id=app.id,
            task_id=task.id,
            token=f"policy-partial-{app.id}",
            status=AssessmentStatus.COMPLETED,
            scoring_partial=True,
            assessment_score=0.0,
            taali_score=95.0,
        )
    )
    db.flush()

    verdict, outputs = evaluate_for_application(
        db, role=role, application_id=int(app.id)
    )

    assert verdict.decision_type == "no_action"
    assert verdict.rule_path == ["assessment_grading_incomplete"]
    assert outputs == {}
