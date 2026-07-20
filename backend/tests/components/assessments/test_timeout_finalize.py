"""Tests for server-side timeout finalization of abandoned assessments.

Covers the fix for the "worked 72 minutes then EXPIRED with no result" funnel
leak: a candidate who starts an assessment and walks away without submitting must
have their work frozen (COMPLETED_DUE_TO_TIMEOUT) by a server-side sweep and
queued for grading, NOT discarded by the cleanup reaper.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.components.assessments import service as assessments_svc
from app.components.assessments.submission_runtime import _build_submission_artifact
from app.models.assessment import Assessment, AssessmentStatus
from app.models.role import Role
from app.services.task_catalog import PERSISTED_TASK_SPEC_KEYS
from app.tasks import agent_tasks, rubric_retry_tasks
from app.tasks.assessment_tasks import (
    cleanup_expired_assessments,
    finalize_timed_out_assessments,
)
from tests.conftest import verify_user


def _register_and_login(client):
    client.post("/api/v1/auth/register", json={
        "email": "timeout@example.com",
        "password": "testpass123",
        "full_name": "Timeout User",
        "organization_name": "Timeout Org",
    })
    verify_user("timeout@example.com")
    login = client.post("/api/v1/auth/jwt/login", data={
        "username": "timeout@example.com",
        "password": "testpass123",
    })
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_task(client, headers):
    canonical_path = Path(__file__).resolve().parents[3] / "tasks" / "data_eng_bronze_ingestion.json"
    spec = json.loads(canonical_path.read_text(encoding="utf-8"))
    payload = {
        "name": spec["name"],
        "description": spec["scenario"][:500],
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": spec["duration_minutes"],
        "starter_code": "print('x')",
        "test_code": "def test_ok(): assert True",
        "task_key": "timeout-task",
        "role": spec["role"],
        "scenario": spec["scenario"],
        "repo_structure": spec["repo_structure"],
        "evaluation_rubric": spec["evaluation_rubric"],
        "extra_data": {
            key: value
            for key, value in spec.items()
            if key not in PERSISTED_TASK_SPEC_KEYS
        },
    }
    resp = client.post("/api/v1/tasks", json=payload, headers=headers)
    return resp.json()


def _make_assessment(client, db, headers, task_id, *, status, started_minutes_ago,
                     duration_minutes=30, expires_in_days=7):
    """Create an assessment via the API (PENDING) then force it into the state we
    want directly in the DB — avoids driving the E2B-backed /start flow."""
    resp = client.post("/api/v1/assessments", json={
        "candidate_email": f"cand-{started_minutes_ago}-{status.value}@example.com",
        "candidate_name": "Cand",
        "task_id": task_id,
        "duration_minutes": duration_minutes,
    }, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    aid = resp.json()["id"]

    now = datetime.now(timezone.utc)
    a = db.query(Assessment).filter(Assessment.id == aid).first()
    a.status = status
    a.duration_minutes = duration_minutes
    a.started_at = (now - timedelta(minutes=started_minutes_ago)) if started_minutes_ago is not None else None
    a.expires_at = now + timedelta(days=expires_in_days)
    a.e2b_session_id = "sandbox-x"
    db.commit()
    db.refresh(a)
    return a


def test_finalize_freezes_queues_and_marks_timeout(client, db, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(client, db, headers, task["id"],
                         status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=40)

    def fake_submit(
        assessment,
        final_code,
        tab_switch_count,
        _db,
        *,
        wake_agent_on_commit=True,
        defer_scoring=False,
        enqueue_rubric_retry_on_commit=True,
    ):
        assert wake_agent_on_commit is False
        assert defer_scoring is True
        assert enqueue_rubric_retry_on_commit is False
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.scoring_partial = True
        _db.commit()
        return {"success": True, "grading_status": "pending"}

    dispatched: list[int] = []
    monkeypatch.setattr(assessments_svc, "submit_assessment", fake_submit)
    monkeypatch.setattr(
        rubric_retry_tasks.retry_incomplete_rubric_scoring,
        "delay",
        lambda assessment_id: dispatched.append(int(assessment_id)),
    )

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "finalized"
    assert result["scoring_failed"] is False
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert a.completed_due_to_timeout is True
    assert a.scoring_failed in (False, None)
    assert a.scoring_partial is True
    assert a.taali_score is None
    assert dispatched == [a.id]
    assert a.completed_at is not None


def test_timed_out_finalization_defers_role_wake_until_grading_completes(
    client, db, monkeypatch
):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    role_response = client.post(
        "/api/v1/roles",
        json={"name": "Timeout Agent Role"},
        headers=headers,
    )
    assert role_response.status_code in (200, 201), role_response.text
    role = db.get(Role, role_response.json()["id"])
    role.agentic_mode_enabled = True

    a = _make_assessment(
        client,
        db,
        headers,
        task["id"],
        status=AssessmentStatus.IN_PROGRESS,
        started_minutes_ago=40,
    )
    a.role_id = role.id
    db.commit()

    def fake_submit(
        assessment,
        final_code,
        tab_switch_count,
        _db,
        *,
        wake_agent_on_commit=True,
        defer_scoring=False,
        enqueue_rubric_retry_on_commit=True,
    ):
        assert wake_agent_on_commit is False
        assert defer_scoring is True
        assert enqueue_rubric_retry_on_commit is False
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.scoring_partial = True
        _db.commit()
        return {"success": True, "grading_status": "pending"}

    wake_calls: list[tuple[int, bool]] = []
    dispatched: list[int] = []
    monkeypatch.setattr(assessments_svc, "submit_assessment", fake_submit)
    monkeypatch.setattr(
        rubric_retry_tasks.retry_incomplete_rubric_scoring,
        "delay",
        lambda assessment_id: dispatched.append(int(assessment_id)),
    )
    monkeypatch.setattr(
        agent_tasks.agent_cohort_tick_role,
        "delay",
        lambda role_id, *, activation: wake_calls.append((role_id, activation)),
    )

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "finalized"
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert dispatched == [a.id]
    assert wake_calls == []


def test_finalize_capture_failure_stays_retryable(client, db, monkeypatch):
    """A transient sandbox failure keeps the timed-out row for the next sweep."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(client, db, headers, task["id"],
                         status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=40)

    def boom(*_args, **_kwargs):
        raise RuntimeError("e2b sandbox expired")

    monkeypatch.setattr(assessments_svc, "submit_assessment", boom)

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "capture_failed"
    assert result["scoring_failed"] is True
    db.refresh(a)
    assert a.status == AssessmentStatus.IN_PROGRESS
    assert a.scoring_failed is True
    assert a.completed_at is None


def test_finalize_yields_to_racing_candidate_submit(client, db, monkeypatch):
    """If the candidate's own submit won the atomic claim (409), don't relabel it
    as a timeout completion."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(client, db, headers, task["id"],
                         status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=40)

    def already_submitted(assessment, *_args, **_kwargs):
        artifact = _build_submission_artifact({"main.py": "print('submitted')\n"})
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.submission_artifact = artifact
        assessment.submission_artifact_sha256 = artifact["sha256"]
        assessment.submission_artifact_captured_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=409, detail="Assessment already submitted")

    monkeypatch.setattr(assessments_svc, "submit_assessment", already_submitted)

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "already_submitted"
    db.refresh(a)
    assert a.completed_due_to_timeout in (False, None)
    assert a.status != AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT


def test_finalize_rejects_terminal_409_without_a_durable_receipt(client, db, monkeypatch):
    """A terminal status alone cannot prove that candidate work was frozen."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(
        client,
        db,
        headers,
        task["id"],
        status=AssessmentStatus.IN_PROGRESS,
        started_minutes_ago=40,
    )

    def terminal_without_artifact(assessment, *_args, **_kwargs):
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=409, detail="Assessment already submitted")

    monkeypatch.setattr(assessments_svc, "submit_assessment", terminal_without_artifact)

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "capture_failed"
    assert result["scoring_failed"] is True
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED
    assert a.submission_artifact is None
    assert a.scoring_failed is True


@pytest.mark.parametrize("operation_kind", ["save", "claude_chat"])
def test_finalize_keeps_live_workspace_lease_conflicts_retryable(
    client, db, operation_kind,
):
    """A live save/Claude lease is not proof that submission completed."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(
        client,
        db,
        headers,
        task["id"],
        status=AssessmentStatus.IN_PROGRESS,
        started_minutes_ago=40,
    )
    a.runtime_operation_id = f"live-{operation_kind}"
    a.runtime_operation_kind = operation_kind
    a.runtime_operation_started_at = datetime.now(timezone.utc)
    db.commit()

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "capture_failed"
    assert result["scoring_failed"] is True
    db.refresh(a)
    assert a.status == AssessmentStatus.IN_PROGRESS
    assert a.submission_artifact is None
    assert a.runtime_operation_id == f"live-{operation_kind}"
    assert a.runtime_operation_kind == operation_kind
    assert a.scoring_failed is True
    assert any(
        event.get("event_type") == "auto_submit_timeout_capture_failed"
        for event in (a.timeline or [])
    )


def test_finalize_skips_already_terminal(client, db):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(client, db, headers, task["id"],
                         status=AssessmentStatus.COMPLETED, started_minutes_ago=40)

    result = assessments_svc.finalize_timed_out_assessment(a, db)
    assert result["status"] == "skipped"
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED


def test_cleanup_does_not_discard_in_progress_work(client, db):
    """Regression: cleanup must NOT mark a timed-out IN_PROGRESS row EXPIRED (the
    old behaviour discarded the candidate's work). It must still expire PENDING."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)

    in_progress = _make_assessment(client, db, headers, task["id"],
                                   status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=180)
    pending = _make_assessment(client, db, headers, task["id"],
                               status=AssessmentStatus.PENDING, started_minutes_ago=None,
                               expires_in_days=-1)

    cleanup_expired_assessments()

    db.expire_all()
    assert db.get(Assessment,in_progress.id).status == AssessmentStatus.IN_PROGRESS
    assert db.get(Assessment,pending.id).status == AssessmentStatus.EXPIRED


def test_sweep_finalizes_timed_out_and_skips_active(client, db, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)

    expired = _make_assessment(client, db, headers, task["id"],
                               status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=40)
    active = _make_assessment(client, db, headers, task["id"],
                              status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=5)

    def fake_submit(
        assessment,
        final_code,
        tab_switch_count,
        _db,
        *,
        wake_agent_on_commit=True,
        defer_scoring=False,
        enqueue_rubric_retry_on_commit=True,
    ):
        assert wake_agent_on_commit is False
        assert defer_scoring is True
        assert enqueue_rubric_retry_on_commit is False
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.scoring_partial = True
        _db.commit()
        return {"success": True, "grading_status": "pending"}

    dispatched: list[int] = []
    monkeypatch.setattr(assessments_svc, "submit_assessment", fake_submit)
    monkeypatch.setattr(
        rubric_retry_tasks.retry_incomplete_rubric_scoring,
        "delay",
        lambda assessment_id: dispatched.append(int(assessment_id)),
    )

    summary = finalize_timed_out_assessments()

    assert summary["finalized"] == 1
    assert summary["skipped"] == 1
    assert dispatched == [expired.id]
    db.expire_all()
    assert db.get(Assessment,expired.id).status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert db.get(Assessment,active.id).status == AssessmentStatus.IN_PROGRESS
