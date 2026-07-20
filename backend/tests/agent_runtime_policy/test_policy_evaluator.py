"""policy_evaluator: end-to-end sub-agents → engine bridge."""

from __future__ import annotations

from types import SimpleNamespace

from app.agent_runtime import policy_evaluator
from app.agent_runtime.contracts import StructuredIntent
from app.agent_runtime.policy_evaluator import evaluate_for_application
from app.models.assessment import Assessment, AssessmentStatus
from app.models.task import Task

from .conftest import add_event, make_world


def test_policy_subagents_receive_bounded_intent_with_latest_answer(db, monkeypatch):
    _org, role, _, app = make_world(db)
    previous = "OLDEST ANSWER " + ("prior context " * 180)
    latest = (
        "LATEST MUST-HAVE: candidates must overlap Dubai mornings.\n\n"
        "Keep this second paragraph intact."
    )
    intent = SimpleNamespace(
        version=3,
        structured=StructuredIntent(),
        free_text=f"{previous.strip()}\n\n{latest}",
        latest_free_text=latest,
    )
    monkeypatch.setattr(
        policy_evaluator,
        "fetch_active_intent",
        lambda *_args, **_kwargs: intent,
    )
    captured: dict = {}

    def _capture_outputs(*_args, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(
        policy_evaluator,
        "_gather_sub_agent_outputs",
        _capture_outputs,
    )

    evaluate_for_application(db, role=role, application_id=int(app.id))

    notes = captured["role_intent_extra"]["free_text"]
    assert len(notes) == 1200
    assert "OLDEST ANSWER" not in notes
    assert latest in notes
    assert notes.endswith(latest)
    assert "omitted" in notes

    intent.free_text = None
    intent.latest_free_text = None
    captured.clear()
    evaluate_for_application(db, role=role, application_id=int(app.id))
    assert captured["role_intent_extra"]["free_text"] is None


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
    app.pre_screen_score_100 = 90.0
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
