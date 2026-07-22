"""policy_evaluator: end-to-end sub-agents → engine bridge."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agent_runtime import policy_evaluator
from app.agent_runtime.contracts import StructuredIntent
from app.agent_runtime.policy_evaluator import evaluate_for_application
from app.models.assessment import Assessment, AssessmentStatus
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.task import Task

from .conftest import add_event, make_world


def test_policy_subagents_receive_bounded_intent_with_latest_answer(db, monkeypatch):
    _org, role, _, app = make_world(db)
    app.pre_screen_score_100 = 70.0
    app.cv_match_score = 70.0
    db.flush()
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


def test_cold_unscored_policy_stops_before_paid_subagents(db, monkeypatch):
    _org, role, _, app = make_world(db)

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("cold application must use the canonical scorer")

    monkeypatch.setattr(policy_evaluator, "_gather_sub_agent_outputs", _must_not_run)

    verdict, outputs = evaluate_for_application(
        db, role=role, application_id=int(app.id)
    )

    assert verdict.decision_type == "no_action"
    assert verdict.rule_path == ["score_refresh_required"]
    assert outputs == {}


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


@pytest.mark.parametrize("status", ["stale", "pending", "running", "error"])
def test_policy_refuses_persisted_scores_until_latest_job_is_done(
    db, monkeypatch, status
):
    _org, role, _, app = make_world(db)
    app.cv_match_details = {"role_fit_score": 80.0, "dimension_scores": {}}
    app.pre_screen_score_100 = 80.0
    db.add(CvScoreJob(application_id=int(app.id), role_id=int(role.id), status=status))
    db.flush()

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("stale persisted scores must not reach sub-agents")

    monkeypatch.setattr(policy_evaluator, "_gather_sub_agent_outputs", _must_not_run)

    verdict, outputs = evaluate_for_application(
        db, role=role, application_id=int(app.id)
    )

    assert verdict.decision_type == "no_action"
    assert verdict.rule_path == ["score_refresh_required"]
    assert outputs == {}


def test_sister_role_policy_defers_to_role_local_runtime_before_owner_cache(
    db, monkeypatch
):
    org, owner, _, app = make_world(db)
    sister = Role(
        organization_id=int(org.id),
        name="Related backend view",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
        job_spec_text="Hire a backend engineer for a related team.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(sister)
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=int(org.id),
            role_id=int(sister.id),
            candidate_id=int(app.candidate_id),
            source_application_id=int(app.id),
            ats_application_id=int(app.id),
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="initial_snapshot",
            spec_fingerprint="policy-related-runtime",
        )
    )
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(owner.id),
            status="stale",
        )
    )
    db.flush()
    gather = MagicMock(side_effect=AssertionError("must not read owner caches"))
    monkeypatch.setattr(policy_evaluator, "_gather_sub_agent_outputs", gather)

    verdict, _outputs = evaluate_for_application(
        db, role=sister, application_id=int(app.id)
    )

    gather.assert_not_called()
    assert verdict.rule_path == ["related_role_runtime_required"]


def test_sister_role_without_owner_stops_before_subagents(db, monkeypatch):
    org, _owner, _candidate, app = make_world(db)
    sister = Role(
        organization_id=int(org.id),
        name="Broken related role",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=None,
        job_spec_text="Related role",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(sister)
    db.flush()
    gather = MagicMock(side_effect=AssertionError("must not run sub-agents"))
    monkeypatch.setattr(policy_evaluator, "_gather_sub_agent_outputs", gather)

    verdict, outputs = evaluate_for_application(
        db, role=sister, application_id=int(app.id)
    )

    assert verdict.rule_path == ["application_missing"]
    assert outputs == {}
    gather.assert_not_called()


@pytest.mark.parametrize("owner_state", ["deleted", "cross_org"])
def test_sister_role_invalid_owner_stops_before_subagents(
    db, monkeypatch, owner_state
):
    org, owner, _candidate, app = make_world(db)
    owner_id = int(owner.id)
    if owner_state == "deleted":
        owner.deleted_at = datetime.now(timezone.utc)
    else:
        other_org = Organization(
            name="Other policy org",
            slug=f"other-policy-org-{id(app)}",
        )
        db.add(other_org)
        db.flush()
        other_owner = Role(
            organization_id=int(other_org.id),
            name="Foreign ATS owner",
            source="manual",
        )
        db.add(other_owner)
        db.flush()
        owner_id = int(other_owner.id)
    sister = Role(
        organization_id=int(org.id),
        name="Invalid related role",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner_id,
        job_spec_text="Related role",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(sister)
    db.flush()
    gather = MagicMock(side_effect=AssertionError("must not run sub-agents"))
    monkeypatch.setattr(policy_evaluator, "_gather_sub_agent_outputs", gather)

    verdict, outputs = evaluate_for_application(
        db, role=sister, application_id=int(app.id)
    )

    assert verdict.rule_path == ["application_missing"]
    assert outputs == {}
    gather.assert_not_called()


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


def test_application_from_another_role_stops_before_subagents(db, monkeypatch):
    org, _owner_role, _candidate, app = make_world(db)
    other_role = Role(
        organization_id=int(org.id),
        name="Different role",
        source="manual",
        description="Different requirements",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(other_role)
    db.flush()

    gather = MagicMock(side_effect=AssertionError("must not run sub-agents"))
    monkeypatch.setattr(policy_evaluator, "_gather_sub_agent_outputs", gather)

    verdict, outputs = evaluate_for_application(
        db,
        role=other_role,
        application_id=int(app.id),
    )

    assert verdict.decision_type == "no_action"
    assert verdict.rule_path == ["application_role_mismatch"]
    assert outputs == {}
    gather.assert_not_called()


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
