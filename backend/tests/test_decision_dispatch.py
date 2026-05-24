"""Async, serialized decision dispatch (batch-approve foundation).

A recruiter approve — single or a 100-row bulk — becomes ONE background job
(BackgroundJobRun, kind 'decision_batch') that drains the Workable writebacks
sequentially per org so a batch can't breach the rate limit. Approved decisions
sit in the queue as 'processing' (optimistic, greyed in the UI); a decision
whose Workable writeback fails is returned to the queue rather than lost.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.actions import approve_decision as approve_decision_action
from app.actions.types import ACTOR_RECRUITER, Actor
from app.models.agent_decision import AgentDecision
from app.models.background_job_run import JOB_KIND_DECISION_BATCH, BackgroundJobRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.services import background_job_runs
from app.services.background_job_runs import SCOPE_KIND_ORG
from app.services.workable_actions_service import (
    WorkableWritebackError,
    disqualify_candidate_in_workable,
    strict_workable_writes,
)
from app.tasks.workable_tasks import run_workable_op_task


def _seed(db, *, workable_connected=False):
    org = Organization(
        name="O",
        slug=f"o-{id(db)}",
        workable_connected=workable_connected,
        workable_access_token=("tok" if workable_connected else None),
        workable_subdomain=("acme" if workable_connected else None),
        workable_config=(
            {
                "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
                "workable_actor_member_id": "m1",
                "workable_disqualify_reason_id": "r1",
            }
            if workable_connected
            else {}
        ),
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    user = User(
        email=f"r-{id(db)}@x.test",
        hashed_password="x",
        full_name="Rec",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return org, role, user


def _add_decision(db, org, role, *, status="processing", decision_type="skip_assessment_reject", workable_linked=False):
    cand = Candidate(organization_id=org.id, email=f"c{id(object())}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        workable_candidate_id=("wkbl_1" if workable_linked else None),
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        agent_run_id=None,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning="x",
        evidence={},
        model_version="pre_screen_v1",
        prompt_version="pre_screen_threshold.v1",
        idempotency_key=f"pre_screen_reject:{int(app.id)}",
        active_capabilities={},
        token_spend={},
    )
    db.add(decision)
    db.flush()
    return app, decision


# --- strict-mode gating primitive ------------------------------------------


def test_strict_mode_makes_disqualify_raise(db):
    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    db.commit()
    with patch("app.services.workable_actions_service.WorkableService") as mock_svc:
        mock_svc.return_value.disqualify_candidate.return_value = {"success": False, "error": "429"}
        with strict_workable_writes():
            with pytest.raises(WorkableWritebackError) as ei:
                disqualify_candidate_in_workable(org=org, app=app, role=role, reason="x")
    assert ei.value.code == "api_error"
    assert ei.value.retriable is True


def test_non_strict_disqualify_returns_dict(db):
    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    db.commit()
    with patch("app.services.workable_actions_service.WorkableService") as mock_svc:
        mock_svc.return_value.disqualify_candidate.return_value = {"success": False, "error": "boom"}
        result = disqualify_candidate_in_workable(org=org, app=app, role=role, reason="x")
    assert result["success"] is False and result["code"] == "api_error"


def test_reject_application_propagates_under_strict(db, monkeypatch):
    """Under strict mode reject_application.run lets a real Workable failure
    propagate instead of swallowing it — what lets the batch requeue."""
    from app.actions import reject_application
    from app.platform.config import settings as cfg

    monkeypatch.setattr(cfg, "MVP_DISABLE_WORKABLE", False)
    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    db.commit()
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user.id))
    with patch("app.services.workable_actions_service.WorkableService") as mock_svc:
        mock_svc.return_value.disqualify_candidate.return_value = {"success": False, "error": "429"}
        with strict_workable_writes():
            with pytest.raises(WorkableWritebackError):
                reject_application.run(db, actor, organization_id=int(org.id), application_id=int(app.id))


# --- batch task -------------------------------------------------------------


def test_batch_success_approves_and_records_job(db):
    org, role, user = _seed(db, workable_connected=False)
    app, decision = _add_decision(db, org, role, status="processing")
    db.commit()
    job_id = background_job_runs.create_run(
        kind=JOB_KIND_DECISION_BATCH, scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id), organization_id=int(org.id),
        counters={"total": 1}, status="queued",
    )
    out = run_workable_op_task.run(
        job_run_id=job_id, organization_id=int(org.id), op_type="approve_decisions",
        payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
    )
    assert out["status"] == "completed" and out["succeeded"] == 1
    db.expire_all()
    assert db.query(AgentDecision).get(decision.id).status == "approved"
    assert db.query(CandidateApplication).get(app.id).application_outcome == "rejected"
    job = db.query(BackgroundJobRun).get(job_id)
    assert job.status == "completed"
    assert job.counters["succeeded"] == 1
    assert job.finished_at is not None


def test_batch_requeues_failed_decision_to_queue(db):
    org, role, user = _seed(db)
    app, decision = _add_decision(db, org, role, status="processing")
    db.commit()
    job_id = background_job_runs.create_run(
        kind=JOB_KIND_DECISION_BATCH, scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id), organization_id=int(org.id), counters={"total": 1},
    )
    err = WorkableWritebackError(action="disqualify", code="not_writeable", message="no scope", retriable=False)
    with patch("app.actions.approve_decision.run", side_effect=err):
        out = run_workable_op_task.run(
            job_run_id=job_id, organization_id=int(org.id), op_type="approve_decisions",
            payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
        )
    assert out["status"] == "completed_with_errors" and out["requeued"] == 1
    db.expire_all()
    refreshed = db.query(AgentDecision).get(decision.id)
    assert refreshed.status == "pending", "must return to the queue, not be lost"
    assert "Workable writeback failed" in (refreshed.resolution_note or "")
    job = db.query(BackgroundJobRun).get(job_id)
    assert job.status == "completed_with_errors" and job.counters["requeued"] == 1


def test_batch_skips_non_processing(db):
    """Idempotent: a row no longer 'processing' is skipped (not re-run)."""
    org, role, user = _seed(db)
    app, decision = _add_decision(db, org, role, status="approved")
    db.commit()
    with patch("app.actions.approve_decision.run") as mock_run:
        out = run_workable_op_task.run(
            job_run_id=None, organization_id=int(org.id), op_type="approve_decisions",
            payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
        )
    assert out["succeeded"] == 0
    assert not mock_run.called


# --- enqueue (optimistic flip + one job + eager end-to-end) -----------------


def test_enqueue_batch_flips_creates_one_job_and_completes(db):
    org, role, user = _seed(db, workable_connected=False)
    app1, d1 = _add_decision(db, org, role, status="pending")
    app2, d2 = _add_decision(db, org, role, status="pending")
    db.commit()
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user.id))
    result = approve_decision_action.enqueue_batch(
        db, actor, organization_id=int(org.id), decision_ids=[int(d1.id), int(d2.id)]
    )
    assert sorted(result["accepted"]) == sorted([int(d1.id), int(d2.id)])
    assert result["job_run_id"] is not None
    assert result["failures"] == []
    db.expire_all()
    # Eager Celery drained the batch inline → both approved.
    assert db.query(AgentDecision).get(d1.id).status == "approved"
    assert db.query(AgentDecision).get(d2.id).status == "approved"
    # Exactly one background job for the whole request.
    jobs = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.organization_id == org.id,
            BackgroundJobRun.kind == JOB_KIND_DECISION_BATCH,
        )
        .all()
    )
    assert len(jobs) == 1
    assert jobs[0].counters["succeeded"] == 2


def test_enqueue_batch_reports_non_pending_failures(db):
    org, role, user = _seed(db)
    app1, d1 = _add_decision(db, org, role, status="pending")
    app2, d2 = _add_decision(db, org, role, status="approved")  # already resolved
    db.commit()
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user.id))
    result = approve_decision_action.enqueue_batch(
        db, actor, organization_id=int(org.id), decision_ids=[int(d1.id), int(d2.id)]
    )
    assert result["accepted"] == [int(d1.id)]
    assert len(result["failures"]) == 1
    assert result["failures"][0]["decision_id"] == int(d2.id)
    assert result["failures"][0]["status_code"] == 409


def test_enqueue_one_rejects_non_pending(db):
    from fastapi import HTTPException

    org, role, user = _seed(db)
    app, decision = _add_decision(db, org, role, status="approved")
    db.commit()
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user.id))
    with pytest.raises(HTTPException) as ei:
        approve_decision_action.enqueue_one(
            db, actor, organization_id=int(org.id), decision_id=int(decision.id)
        )
    assert ei.value.status_code == 409


def test_enqueue_one_success_completes_eager(db):
    org, role, user = _seed(db, workable_connected=False)
    app, decision = _add_decision(db, org, role, status="pending")
    db.commit()
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user.id))
    returned = approve_decision_action.enqueue_one(
        db, actor, organization_id=int(org.id), decision_id=int(decision.id), note="ok"
    )
    assert returned is not None
    db.expire_all()
    assert db.query(AgentDecision).get(decision.id).status == "approved"


# --- other ops through the generic runner -----------------------------------


def test_override_op_requeues_on_workable_failure(db):
    """A gated override (advance) whose Workable move fails returns the decision
    to the queue via the runner's surface step."""
    org, role, user = _seed(db)
    app, decision = _add_decision(
        db, org, role, status="processing", decision_type="advance_to_interview"
    )
    db.commit()
    err = WorkableWritebackError(action="move", code="not_writeable", message="no scope", retriable=False)
    with patch("app.actions.override_decision.run", side_effect=err):
        out = run_workable_op_task.run(
            job_run_id=None, organization_id=int(org.id), op_type="override_decision",
            payload={"decision_id": int(decision.id), "user_id": int(user.id), "override_action": "advance"},
        )
    assert out["status"] == "failed"
    db.expire_all()
    assert db.query(AgentDecision).get(decision.id).status == "pending"


def test_move_stage_op_success_sets_stage(db):
    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    db.commit()
    success = {"success": True, "action": "move", "config": {"actor_member_id": "m1"}}
    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable", return_value=success
    ) as mk:
        out = run_workable_op_task.run(
            job_run_id=None, organization_id=int(org.id), op_type="move_stage",
            payload={"application_id": int(app.id), "user_id": int(user.id),
                     "target_stage": "Technical Interview", "reason": None},
        )
    assert out["status"] == "completed" and mk.called
    db.expire_all()
    assert db.query(CandidateApplication).get(app.id).workable_stage == "Technical Interview"


def test_post_note_op_raises_retriable_on_failure(db):
    """A failed note post raises a retriable WorkableWritebackError so the shell
    retries (tested at the handler level to avoid eager-retry recursion)."""
    from app.services import workable_op_runner as runner

    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    db.commit()
    with patch("app.domains.integrations_notifications.adapters.build_workable_adapter") as mk:
        mk.return_value.post_candidate_comment.return_value = {"success": False, "error": "429"}
        with pytest.raises(WorkableWritebackError) as ei:
            runner.execute_op(
                db, organization_id=int(org.id), op_type="post_note",
                payload={"application_id": int(app.id), "user_id": int(user.id), "body": "hi"},
            )
    assert ei.value.retriable is True
