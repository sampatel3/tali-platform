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
from app.decision_policy.bootstrap import bootstrap_org
from app.models.agent_decision import AgentDecision
from app.models.background_job_run import (
    JOB_KIND_DECISION_BATCH,
    JOB_KIND_WORKABLE_OP,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.job_hiring_team import TEAM_ROLE_RECRUITER, JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
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
                "workable_writeback": True,
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
    db.add(
        JobHiringTeam(
            organization_id=org.id,
            role_id=role.id,
            user_id=user.id,
            team_role=TEAM_ROLE_RECRUITER,
        )
    )
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


def _tracked_run_id(org_id: int, op_type: str) -> int:
    kind = JOB_KIND_DECISION_BATCH if op_type == "approve_decisions" else JOB_KIND_WORKABLE_OP
    run_id = background_job_runs.create_run(
        kind=kind,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org_id),
        organization_id=int(org_id),
        counters={"op_type": op_type},
        status="queued",
    )
    assert run_id is not None
    return int(run_id)


def test_mixed_provider_decision_batch_acquires_both_mutexes(db, monkeypatch):
    from app.components.integrations.bullhorn.sync_runner import (
        BULLHORN_ORG_MUTEX_NAMESPACE,
    )
    from app.platform.config import settings
    from app.tasks import assessment_tasks
    from app.tasks.assessment_tasks import _WORKABLE_ORG_MUTEX_KEY_PREFIX

    org, role, user = _seed(db, workable_connected=True)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    workable_app, workable_decision = _add_decision(
        db, org, role, workable_linked=True
    )
    bullhorn_app, bullhorn_decision = _add_decision(db, org, role)
    bullhorn_app.source = "bullhorn"
    bullhorn_app.bullhorn_job_submission_id = "submission-mixed"
    db.commit()
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)

    acquired: list[str] = []

    def _acquire(_org_id, *, namespace, **_kwargs):
        acquired.append(namespace)
        return (object(), f"{namespace}:{org.id}", None)

    monkeypatch.setattr(assessment_tasks, "_acquire_workable_org_mutex", _acquire)
    monkeypatch.setattr(assessment_tasks, "_release_workable_org_mutex", lambda *_: None)
    monkeypatch.setattr(assessment_tasks, "mark_workable_op_pending", lambda *_: None)
    with patch(
        "app.services.workable_op_runner.execute_op",
        return_value={"succeeded": 2, "failed": 0},
    ):
        result = run_workable_op_task.run(
            job_run_id=_tracked_run_id(int(org.id), "approve_decisions"),
            organization_id=int(org.id),
            op_type="approve_decisions",
            payload={
                "decision_ids": [
                    int(workable_decision.id),
                    int(bullhorn_decision.id),
                ],
                "user_id": int(user.id),
            },
        )

    assert set(acquired) == {
        _WORKABLE_ORG_MUTEX_KEY_PREFIX,
        BULLHORN_ORG_MUTEX_NAMESPACE,
    }
    assert acquired == sorted(acquired)
    assert result["status"] == "completed"


def test_bullhorn_op_fails_closed_when_redis_lock_state_is_unknown(db, monkeypatch):
    from app.platform.config import settings
    from app.tasks import assessment_tasks
    from app.tasks import workable_tasks

    org, role, user = _seed(db)
    org.bullhorn_connected = True
    org.bullhorn_username = "api-user"
    org.bullhorn_client_id = "client-id"
    org.bullhorn_client_secret = "encrypted-secret"
    org.bullhorn_refresh_token = "encrypted-refresh"
    role.source = "bullhorn"
    role.bullhorn_job_order_id = "job-lock"
    app, _decision = _add_decision(db, org, role)
    app.source = "bullhorn"
    app.bullhorn_job_submission_id = "submission-lock"
    db.commit()
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    monkeypatch.setattr(
        assessment_tasks, "_acquire_workable_org_mutex", lambda *_a, **_k: False
    )
    monkeypatch.setattr(assessment_tasks, "mark_workable_op_pending", lambda *_: None)

    with patch.object(workable_tasks.run_workable_op_task, "apply_async") as retry, patch(
        "app.services.workable_op_runner.execute_op"
    ) as execute:
        result = run_workable_op_task.run(
            job_run_id=_tracked_run_id(int(org.id), "manual_outcome"),
            organization_id=int(org.id),
            op_type="manual_outcome",
            payload={
                "application_id": int(app.id),
                "target_outcome": "rejected",
                "user_id": int(user.id),
            },
        )

    assert result["status"] == "lock_wait_requeued"
    retry.assert_called_once()
    execute.assert_not_called()


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
        counters={"total": 1, "op_type": "approve_decisions"}, status="queued",
    )
    out = run_workable_op_task.run(
        job_run_id=job_id, organization_id=int(org.id), op_type="approve_decisions",
        payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
    )
    assert out["status"] == "completed" and out["succeeded"] == 1
    db.expire_all()
    assert db.get(AgentDecision, decision.id).status == "approved"
    assert db.get(CandidateApplication, app.id).application_outcome == "rejected"
    job = db.get(BackgroundJobRun, job_id)
    assert job.status == "completed"
    assert job.counters["succeeded"] == 1
    assert job.finished_at is not None


def test_batch_requeues_failed_decision_to_queue(db):
    org, role, user = _seed(db)
    app, decision = _add_decision(db, org, role, status="processing")
    db.commit()
    job_id = background_job_runs.create_run(
        kind=JOB_KIND_DECISION_BATCH, scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id), organization_id=int(org.id),
        counters={"total": 1, "op_type": "approve_decisions"}, status="queued",
    )
    err = WorkableWritebackError(action="disqualify", code="not_writeable", message="no scope", retriable=False)
    with patch("app.actions.approve_decision.run", side_effect=err):
        out = run_workable_op_task.run(
            job_run_id=job_id, organization_id=int(org.id), op_type="approve_decisions",
            payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
        )
    assert out["status"] == "completed_with_errors" and out["requeued"] == 1
    db.expire_all()
    refreshed = db.get(AgentDecision, decision.id)
    assert refreshed.status == "pending", "must return to the queue, not be lost"
    assert "Workable didn't accept the update" in (refreshed.resolution_note or "")
    job = db.get(BackgroundJobRun, job_id)
    assert job.status == "completed_with_errors" and job.counters["requeued"] == 1


def test_batch_requeues_send_assessment_when_role_has_no_task(db):
    """A task removed after queueing makes the send card stale at dispatch.

    The policy-current guard deliberately runs before the lower-level send
    action's missing-task validation. Model a real dispatch race: the card is
    a valid ``send_assessment`` while queued, then its active task is unlinked
    before the worker approves it. The worker must fail closed and return the
    card to HITL with the replacement action, never approve or send it.
    """
    org, role, user = _seed(db)
    task = Task(
        organization_id=int(org.id),
        name="Queued assessment",
        is_active=True,
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    app, decision = _add_decision(
        db, org, role, status="processing", decision_type="send_assessment"
    )
    role.score_threshold = 50
    role.auto_reject_threshold_mode = "manual"
    role.auto_skip_assessment = False
    app.cv_match_score = 80
    app.pre_screen_score_100 = 80
    bootstrap_org(db, organization_id=int(org.id))
    db.commit()

    from app.services.bulk_decision_service._shared import (
        recompute_persisted_verdict,
    )

    assert recompute_persisted_verdict(db, role=role, app=app) == "send_assessment"
    role.tasks.remove(task)
    db.commit()

    out = run_workable_op_task.run(
        job_run_id=_tracked_run_id(int(org.id), "approve_decisions"),
        organization_id=int(org.id), op_type="approve_decisions",
        payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
    )
    assert out["status"] == "completed_with_errors" and out["requeued"] == 1
    db.expire_all()
    refreshed = db.get(AgentDecision, decision.id)
    assert refreshed.status == "pending", "must return to the queue, not be approved"
    note = (refreshed.resolution_note or "").lower()
    assert "assessment_stage_decision_stale" in note
    assert "'current_decision_type': 'advance_to_interview'" in note
    assert "unexpected error" not in note


def test_batch_skips_non_processing(db):
    """Idempotent: a row no longer 'processing' is skipped (not re-run)."""
    org, role, user = _seed(db)
    app, decision = _add_decision(db, org, role, status="approved")
    db.commit()
    with patch("app.actions.approve_decision.run") as mock_run:
        out = run_workable_op_task.run(
            job_run_id=_tracked_run_id(int(org.id), "approve_decisions"),
            organization_id=int(org.id), op_type="approve_decisions",
            payload={"decision_ids": [int(decision.id)], "user_id": int(user.id)},
        )
    assert out["succeeded"] == 0
    assert out["skipped"] == 1  # counted as already-resolved, not a failure
    assert not mock_run.called


def test_batch_resolves_per_role_workable_stage(db):
    """A multi-role bulk approve routes each advance to its own role's picked
    Workable stage (the per-role map), falling back to the single stage for a
    role that isn't in the map."""
    org, role_a, user = _seed(db)
    role_b = Role(organization_id=org.id, name="RB", source="manual", agentic_mode_enabled=True)
    role_c = Role(organization_id=org.id, name="RC", source="manual", agentic_mode_enabled=True)
    db.add_all([role_b, role_c])
    db.flush()
    _, da = _add_decision(db, org, role_a, status="processing", decision_type="advance_to_interview")
    _, db_dec = _add_decision(db, org, role_b, status="processing", decision_type="advance_to_interview")
    _, dc = _add_decision(db, org, role_c, status="processing", decision_type="advance_to_interview")
    db.commit()

    seen: dict[int, str | None] = {}

    def _fake_lifecycle(db_, *, decision_id, target_stage=None, **kw):
        seen[int(decision_id)] = target_stage
        d = db_.get(AgentDecision, int(decision_id))
        d.status = "approved"
        db_.commit()
        return {"status": "ok", "decision_id": int(decision_id)}

    with patch(
        "app.services.decision_provider_lifecycle.execute_decision_provider_lifecycle",
        side_effect=_fake_lifecycle,
    ):
        out = run_workable_op_task.run(
            job_run_id=_tracked_run_id(int(org.id), "approve_decisions"),
            organization_id=int(org.id), op_type="approve_decisions",
            payload={
                "decision_ids": [int(da.id), int(db_dec.id), int(dc.id)],
                "user_id": int(user.id),
                # role_c absent → falls back to the single stage.
                "workable_target_stages": {str(role_a.id): "Phone Screen", str(role_b.id): "Onsite"},
                "workable_target_stage": "Fallback",
            },
        )
    assert out["succeeded"] == 3
    assert seen[int(da.id)] == "Phone Screen"
    assert seen[int(db_dec.id)] == "Onsite"
    assert seen[int(dc.id)] == "Fallback"


# --- enqueue (optimistic flip + one job + eager end-to-end) -----------------


def test_enqueue_batch_flips_creates_one_job_and_completes(db):
    org, role, user = _seed(db, workable_connected=False)
    app1, d1 = _add_decision(db, org, role, status="pending")
    app2, d2 = _add_decision(db, org, role, status="pending")
    db.commit()
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user.id))
    result = approve_decision_action.enqueue_batch(
        db,
        actor,
        organization_id=int(org.id),
        decision_ids=[int(d1.id), int(d2.id)],
        expected_decision_types={
            str(d1.id): str(d1.decision_type),
            str(d2.id): str(d2.decision_type),
        },
    )
    assert sorted(result["accepted"]) == sorted([int(d1.id), int(d2.id)])
    assert result["job_run_id"] is not None
    assert result["failures"] == []
    db.expire_all()
    # Eager Celery drained the batch inline → both approved.
    assert db.get(AgentDecision, d1.id).status == "approved"
    assert db.get(AgentDecision, d2.id).status == "approved"
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
        db,
        actor,
        organization_id=int(org.id),
        decision_ids=[int(d1.id), int(d2.id)],
        expected_decision_types={str(d1.id): str(d1.decision_type)},
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
        db,
        actor,
        organization_id=int(org.id),
        decision_id=int(decision.id),
        note="ok",
        expected_decision_type=str(decision.decision_type),
    )
    assert returned is not None
    db.expire_all()
    assert db.get(AgentDecision, decision.id).status == "approved"


def test_enqueue_rejects_when_displayed_decision_type_changed(db):
    from fastapi import HTTPException

    org, role, user = _seed(db)
    _, decision = _add_decision(
        db,
        org,
        role,
        status="pending",
        decision_type="reject",
    )
    db.commit()

    with pytest.raises(HTTPException) as error:
        approve_decision_action.enqueue_one(
            db,
            Actor(type=ACTOR_RECRUITER, user_id=int(user.id)),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            expected_decision_type="send_assessment",
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DECISION_CHANGED"
    assert db.get(AgentDecision, decision.id).status == "pending"


def test_durable_approve_rechecks_type_before_any_reject_effect(db):
    from app.services.workable_op_runner import OP_APPROVE_DECISIONS, execute_op

    org, role, user = _seed(db)
    app, decision = _add_decision(
        db,
        org,
        role,
        status="processing",
        decision_type="reject",
    )
    db.commit()

    with patch(
        "app.actions.approve_decision.reject_application.run",
        side_effect=AssertionError("changed decision reached reject effect"),
    ):
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_APPROVE_DECISIONS,
            payload={
                "decision_ids": [int(decision.id)],
                "user_id": int(user.id),
                "expected_decision_types": {
                    str(decision.id): "send_assessment",
                },
            },
        )

    assert result["requeued"] == 1
    db.expire_all()
    assert db.get(AgentDecision, decision.id).status == "pending"
    assert db.get(CandidateApplication, app.id).application_outcome == "open"


def test_durable_related_reject_rechecks_family_before_any_effect(db):
    from app.services.workable_op_runner import OP_APPROVE_DECISIONS, execute_op

    org, owner, user = _seed(db)
    first_related = _add_related_role(db, org, owner, name="First related")
    app, decision = _add_decision(
        db,
        org,
        owner,
        status="processing",
        decision_type="reject",
    )
    decision.role_id = int(first_related.id)
    displayed_family = _family_payload(owner, first_related)
    _add_related_role(db, org, owner, name="Added after confirmation")
    db.commit()

    with patch(
        "app.actions.approve_decision.reject_application.run",
        side_effect=AssertionError("changed family reached reject effect"),
    ):
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_APPROVE_DECISIONS,
            payload={
                "decision_ids": [int(decision.id)],
                "user_id": int(user.id),
                "expected_decision_types": {str(decision.id): "reject"},
                "expected_role_families": {
                    str(decision.id): displayed_family,
                },
            },
        )

    assert result["requeued"] == 1
    db.expire_all()
    assert db.get(AgentDecision, decision.id).status == "pending"
    assert db.get(CandidateApplication, app.id).application_outcome == "open"


def test_type_incompatible_override_is_rejected_before_enqueue(db):
    from fastapi import HTTPException

    from app.actions import override_decision

    org, role, user = _seed(db)
    _, decision = _add_decision(
        db,
        org,
        role,
        status="pending",
        decision_type="send_assessment",
    )
    db.commit()

    with pytest.raises(HTTPException) as error:
        override_decision.enqueue(
            db,
            Actor(type=ACTOR_RECRUITER, user_id=int(user.id)),
            organization_id=int(org.id),
            decision_id=int(decision.id),
            override_action="advance",
            expected_decision_type="send_assessment",
        )

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "UNSUPPORTED_DECISION_OVERRIDE"
    assert db.get(AgentDecision, decision.id).status == "pending"


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
            job_run_id=_tracked_run_id(int(org.id), "override_decision"),
            organization_id=int(org.id), op_type="override_decision",
            payload={"decision_id": int(decision.id), "user_id": int(user.id), "override_action": "advance"},
        )
    assert out["status"] == "failed"
    db.expire_all()
    assert db.get(AgentDecision, decision.id).status == "pending"


def test_move_stage_op_success_sets_stage(db, monkeypatch):
    from app.platform.config import settings
    from app.services.ats_stage_move_dispatch_snapshot import (
        build_stage_move_dispatch_payload,
    )
    from app.services.ats_stage_move_lifecycle import (
        execute_stage_move_lifecycle,
    )

    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    role.source = "workable"
    role.workable_job_id = "workable-job-1"
    app.source = "workable"
    db.commit()
    payload = {
        **build_stage_move_dispatch_payload(
            app=app,
            owner_role=role,
            provider="workable",
            target_stage="Technical Interview",
        ),
        "user_id": int(user.id),
        "reason": None,
    }

    def lifecycle(db_, *, organization_id, payload):
        def provider(plan):
            assert not db_.in_transaction()
            return {
                "success": True,
                "code": "ok",
                "provider": plan.provider,
                "provider_remote_stage": plan.target_stage,
            }

        return execute_stage_move_lifecycle(
            db_,
            organization_id=organization_id,
            payload=payload,
            provider_call=provider,
        )

    with patch(
        "app.services.ats_stage_move_lifecycle.execute_stage_move_lifecycle",
        side_effect=lifecycle,
    ) as mk:
        out = run_workable_op_task.run(
            job_run_id=_tracked_run_id(int(org.id), "move_stage"),
            organization_id=int(org.id), op_type="move_stage",
            payload=payload,
        )
    assert out["status"] == "completed" and mk.called
    db.expire_all()
    assert db.get(CandidateApplication, app.id).workable_stage == "Technical Interview"


def test_post_note_ambiguous_failure_requires_reconciliation_without_retry(db):
    """An uncertain note result is fenced instead of being blindly reposted."""
    from app.services import workable_op_runner as runner
    from app.services.ats_note_provider import AtsNoteProviderFailure
    from app.services.ats_note_receipt import ATS_NOTE_WRITEBACK_KEY

    org, role, user = _seed(db, workable_connected=True)
    app, decision = _add_decision(db, org, role, workable_linked=True)
    db.commit()
    payload = {
        "application_id": int(app.id),
        "user_id": int(user.id),
        "body": "hi",
        "provider": "workable",
        "provider_target_id": str(app.workable_candidate_id),
        "candidate_provider_id": str(app.workable_candidate_id),
        "note_operation_id": f"test-note:{int(app.id)}",
    }
    failure = AtsNoteProviderFailure(
        code="api_error",
        message="Provider result is uncertain",
        provider_called=None,
    )
    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call",
        side_effect=failure,
    ):
        first = runner.execute_op(
            db,
            organization_id=int(org.id),
            op_type="post_note",
            payload=payload,
        )
    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        second = runner.execute_op(
            db,
            organization_id=int(org.id),
            op_type="post_note",
            payload=payload,
        )

    assert first["status"] == "manual_reconciliation_required"
    assert second["status"] == "manual_reconciliation_required"
    provider.assert_not_called()
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["provider_called"] is None
    assert receipt["manual_reconciliation_required"] is True


# ---------------------------------------------------------------------------
# Lock contention: a queued batch waits (re-enqueues) instead of failing while
# another batch holds the per-org Workable mutex.
# ---------------------------------------------------------------------------


def test_lock_contention_requeues_instead_of_failing(db, monkeypatch):
    """When the per-org mutex is held, the task re-enqueues a fresh copy with an
    incremented lock_attempt (its own large wait budget) rather than timing out
    and failing the batch."""
    from app.tasks import assessment_tasks, workable_tasks

    monkeypatch.setattr(
        assessment_tasks, "_acquire_workable_org_mutex", lambda *a, **k: None
    )
    captured: dict = {}

    def _fake_apply_async(*, kwargs=None, countdown=None):
        captured["kwargs"] = kwargs
        captured["countdown"] = countdown

    monkeypatch.setattr(
        workable_tasks.run_workable_op_task, "apply_async", _fake_apply_async
    )

    org, _role, _user = _seed(db)
    db.commit()
    out = workable_tasks.run_workable_op_task.run(
        job_run_id=_tracked_run_id(int(org.id), "approve_decisions"),
        organization_id=int(org.id),
        op_type="approve_decisions",
        payload={"decision_ids": [1, 2, 3]},
        lock_attempt=0,
    )

    assert out["status"] == "lock_wait_requeued"
    assert out["attempt"] == 1
    assert captured["kwargs"]["lock_attempt"] == 1
    assert captured["kwargs"]["op_type"] == "approve_decisions"
    assert captured["kwargs"]["payload"] == {"decision_ids": [1, 2, 3]}
    assert 5 <= captured["countdown"] <= 15


# --- bulk override (e.g. bulk "Skip & advance") -----------------------------


def test_bulk_override_dispatches_per_decision_with_resolved_stage(db, monkeypatch):
    """bulk-override flips each pending decision and dispatches its override,
    resolving the Workable advance stage per role (same map as bulk approve);
    missing ids are reported as failures, not fatal."""
    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    _, d1 = _add_decision(db, org, role, status="pending", decision_type="reject")
    _, d2 = _add_decision(db, org, role, status="pending", decision_type="reject")
    db.commit()

    calls = []

    def _fake_enqueue(
        db_, actor, *, organization_id, decision_id, override_action, note,
        workable_target_stage, **_kwargs,
    ):
        calls.append((decision_id, override_action, workable_target_stage))

    monkeypatch.setattr(agentic_routes.override_decision_action, "enqueue", _fake_enqueue)

    result = agentic_routes.bulk_override(
        agentic_routes.BulkOverrideBody(
            decision_ids=[d1.id, d2.id, 999_999],
            override_action="advance",
            workable_target_stages={str(role.id): "Phone Screen"},
            expected_decision_types={str(d1.id): "reject", str(d2.id): "reject"},
        ),
        db=db,
        current_user=user,
    )
    assert result.requested == 3
    assert result.accepted == 2
    assert [f.decision_id for f in result.failures] == [999_999]
    assert sorted(c[0] for c in calls) == sorted([d1.id, d2.id])
    assert all(c[1] == "advance" and c[2] == "Phone Screen" for c in calls)


def test_bulk_override_skip_assessment_advance_reclassifies_not_enqueues(db, monkeypatch):
    """Bulk "Skip & advance" reclassifies each card into the advance queue
    (sync, no Workable op) rather than enqueuing an immediate advance."""
    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    _, d1 = _add_decision(db, org, role, status="pending", decision_type="send_assessment")
    _, d2 = _add_decision(db, org, role, status="pending", decision_type="send_assessment")
    db.commit()

    enqueued = []
    reclassified = []

    def _fake_enqueue(db_, actor, **kw):
        enqueued.append(kw.get("decision_id"))

    def _fake_reclassify(db_, actor, *, organization_id, decision_id, note=None, **_kwargs):
        reclassified.append(decision_id)

    monkeypatch.setattr(agentic_routes.override_decision_action, "enqueue", _fake_enqueue)
    monkeypatch.setattr(
        agentic_routes.override_decision_action,
        "reclassify_to_advance_queue",
        _fake_reclassify,
    )

    result = agentic_routes.bulk_override(
        agentic_routes.BulkOverrideBody(
            decision_ids=[d1.id, d2.id],
            override_action="skip_assessment_advance",
            expected_decision_types={
                str(d1.id): "send_assessment",
                str(d2.id): "send_assessment",
            },
        ),
        db=db,
        current_user=user,
    )
    assert result.accepted == 2
    assert enqueued == []  # no immediate-advance / Workable op
    assert sorted(reclassified) == sorted([d1.id, d2.id])


def test_bulk_override_reports_tracking_failure_per_decision_and_continues(db, monkeypatch):
    from app.domains.agentic import routes as agentic_routes
    from app.services.workable_op_runner import AtsJobRunPersistenceError

    org, role, user = _seed(db)
    _, d1 = _add_decision(db, org, role, status="pending", decision_type="reject")
    _, d2 = _add_decision(db, org, role, status="pending", decision_type="reject")
    _, d3 = _add_decision(db, org, role, status="pending", decision_type="reject")
    db.commit()
    calls: list[int] = []

    def _fake_enqueue(db_, actor, *, decision_id, **_kwargs):
        calls.append(int(decision_id))
        if int(decision_id) == int(d2.id):
            raise AtsJobRunPersistenceError("override_decision")

    monkeypatch.setattr(agentic_routes.override_decision_action, "enqueue", _fake_enqueue)

    result = agentic_routes.bulk_override(
        agentic_routes.BulkOverrideBody(
            decision_ids=[d1.id, d2.id, d3.id],
            override_action="reject",
            expected_decision_types={
                str(d1.id): "reject",
                str(d2.id): "reject",
                str(d3.id): "reject",
            },
        ),
        db=db,
        current_user=user,
    )

    assert result.requested == 3
    assert result.accepted == 2
    assert calls == [d1.id, d2.id, d3.id]
    assert [failure.decision_id for failure in result.failures] == [d2.id]
    assert "No provider update was sent for this decision" in result.failures[0].error


def test_bulk_override_rejects_unsupported_action(db):
    """``send_assessment`` is not a bulk override — that's what bulk approve is."""
    from fastapi import HTTPException
    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    _, d1 = _add_decision(db, org, role, status="pending", decision_type="send_assessment")
    db.commit()
    with pytest.raises(HTTPException) as ei:
        agentic_routes.bulk_override(
            agentic_routes.BulkOverrideBody(decision_ids=[d1.id], override_action="send_assessment"),
            db=db,
            current_user=user,
        )
    assert ei.value.status_code == 422


# --- shared-role family confirmation authority -----------------------------


def _add_related_role(db, org, owner, *, name="Related scoring view"):
    related = Role(
        organization_id=org.id,
        name=name,
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    return related


def _family_payload(owner, *related):
    return {
        "owner": {"id": int(owner.id), "name": owner.name},
        "related": [
            {"id": int(role.id), "name": role.name}
            for role in sorted(related, key=lambda row: (row.name.casefold(), row.id))
        ],
    }


def test_single_reject_routes_require_family_confirmation(db, monkeypatch):
    from fastapi import HTTPException

    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    _add_related_role(db, org, role)
    _, approve_decision = _add_decision(
        db, org, role, status="pending", decision_type="reject"
    )
    _, override_decision = _add_decision(
        db, org, role, status="pending", decision_type="send_assessment"
    )
    db.commit()
    approve_enqueue = monkeypatch.setattr(
        agentic_routes.approve_decision_action,
        "enqueue_one",
        lambda *_args, **_kwargs: pytest.fail("stale family reached approve enqueue"),
    )
    override_enqueue = monkeypatch.setattr(
        agentic_routes.override_decision_action,
        "enqueue",
        lambda *_args, **_kwargs: pytest.fail("stale family reached override enqueue"),
    )
    assert approve_enqueue is None and override_enqueue is None

    with pytest.raises(HTTPException) as approve_error:
        agentic_routes.approve(
            approve_decision.id,
            body=agentic_routes.ApproveBody(),
            force=True,
            db=db,
            current_user=user,
        )
    with pytest.raises(HTTPException) as override_error:
        agentic_routes.override(
            override_decision.id,
            body=agentic_routes.OverrideBody(override_action="reject"),
            db=db,
            current_user=user,
        )

    for error in (approve_error.value, override_error.value):
        assert error.status_code == 409
        assert error.detail["code"] == "ROLE_FAMILY_CHANGED"
        assert len(error.detail["current_role_family"]["related"]) == 1


def test_bulk_reject_routes_require_family_map_before_enqueue(db, monkeypatch):
    from fastapi import HTTPException

    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    _add_related_role(db, org, role)
    _, approve_decision = _add_decision(
        db, org, role, status="pending", decision_type="skip_assessment_reject"
    )
    _, override_decision = _add_decision(
        db, org, role, status="pending", decision_type="send_assessment"
    )
    db.commit()
    monkeypatch.setattr(
        agentic_routes.approve_decision_action,
        "enqueue_batch",
        lambda *_args, **_kwargs: pytest.fail("stale family reached batch approve"),
    )
    monkeypatch.setattr(
        agentic_routes.override_decision_action,
        "enqueue",
        lambda *_args, **_kwargs: pytest.fail("stale family reached bulk override"),
    )

    with pytest.raises(HTTPException) as approve_error:
        agentic_routes.bulk_approve(
            agentic_routes.BulkApproveBody(decision_ids=[approve_decision.id]),
            db=db,
            current_user=user,
        )
    with pytest.raises(HTTPException) as override_error:
        agentic_routes.bulk_override(
            agentic_routes.BulkOverrideBody(
                decision_ids=[override_decision.id],
                override_action="reject",
            ),
            db=db,
            current_user=user,
        )

    assert approve_error.value.detail["code"] == "ROLE_FAMILY_CHANGED"
    assert override_error.value.detail["code"] == "ROLE_FAMILY_CHANGED"


def test_exact_family_allows_single_and_bulk_reject_enqueue(db, monkeypatch):
    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    related = _add_related_role(db, org, role)
    _, single_decision = _add_decision(
        db, org, role, status="pending", decision_type="send_assessment"
    )
    _, bulk_decision = _add_decision(
        db, org, role, status="pending", decision_type="reject"
    )
    db.commit()
    family = _family_payload(role, related)
    single_calls: list[int] = []
    bulk_calls: list[list[int]] = []

    def fake_override(_db, _actor, *, decision_id, **_kwargs):
        single_calls.append(int(decision_id))
        return single_decision

    def fake_batch(_db, _actor, *, decision_ids, **_kwargs):
        bulk_calls.append(list(decision_ids))
        return {"accepted": list(decision_ids), "failures": [], "job_run_id": 42}

    monkeypatch.setattr(agentic_routes.override_decision_action, "enqueue", fake_override)
    monkeypatch.setattr(agentic_routes.approve_decision_action, "enqueue_batch", fake_batch)

    agentic_routes.override(
        single_decision.id,
        body=agentic_routes.OverrideBody(
            override_action="reject",
            expected_role_family=family,
            expected_decision_type="send_assessment",
        ),
        db=db,
        current_user=user,
    )
    result = agentic_routes.bulk_approve(
        agentic_routes.BulkApproveBody(
            decision_ids=[bulk_decision.id],
            expected_role_families={str(role.id): family},
            expected_decision_types={str(bulk_decision.id): "reject"},
        ),
        db=db,
        current_user=user,
    )

    assert single_calls == [single_decision.id]
    assert bulk_calls == [[bulk_decision.id]]
    assert result.accepted == 1


def test_family_name_drift_returns_fresh_family_and_blocks_reject(db, monkeypatch):
    from fastapi import HTTPException

    from app.domains.agentic import routes as agentic_routes

    org, role, user = _seed(db)
    related = _add_related_role(db, org, role, name="Displayed related name")
    _, decision = _add_decision(
        db, org, role, status="pending", decision_type="send_assessment"
    )
    db.commit()
    displayed = _family_payload(role, related)
    related.name = "Renamed related role"
    db.commit()
    monkeypatch.setattr(
        agentic_routes.override_decision_action,
        "enqueue",
        lambda *_args, **_kwargs: pytest.fail("renamed family reached enqueue"),
    )

    with pytest.raises(HTTPException) as error:
        agentic_routes.override(
            decision.id,
            body=agentic_routes.OverrideBody(
                override_action="reject",
                expected_role_family=displayed,
            ),
            db=db,
            current_user=user,
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "ROLE_FAMILY_CHANGED"
    assert error.value.detail["current_role_family"]["related"][0]["name"] == (
        "Renamed related role"
    )
