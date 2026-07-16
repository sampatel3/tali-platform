"""Tests for server-side timeout finalization of abandoned assessments.

Covers the fix for the "worked 72 minutes then EXPIRED with no result" funnel
leak: a candidate who starts an assessment and walks away without submitting must
have their work captured + scored (COMPLETED_DUE_TO_TIMEOUT) by a server-side
sweep, NOT discarded by the cleanup reaper.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.components.assessments import service as assessments_svc
from app.models.assessment import Assessment, AssessmentStatus
from app.models.role import Role
from app.tasks import agent_tasks
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
    resp = client.post("/api/v1/tasks", json={
        "name": "Timeout Task",
        "description": "desc",
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": 30,
        "starter_code": "print('x')",
        "test_code": "def test_ok(): assert True",
        "task_key": "timeout-task",
        "role": "Data Engineer",
        "scenario": "scenario",
        "repo_structure": {"files": {"src/main.py": "def run():\n    return 1"}},
        "evaluation_rubric": {"correctness": 1.0},
    }, headers=headers)
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


def test_finalize_scores_and_marks_timeout(client, db, monkeypatch):
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
        enqueue_rubric_retry_on_commit=True,
    ):
        assert wake_agent_on_commit is False
        assert enqueue_rubric_retry_on_commit is False
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.taali_score = 7.5
        _db.commit()
        return {"ok": True}

    monkeypatch.setattr(assessments_svc, "submit_assessment", fake_submit)

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "finalized"
    assert result["scoring_failed"] is False
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert a.completed_due_to_timeout is True
    assert a.scoring_failed in (False, None)
    assert a.taali_score == 7.5  # the score the submit pipeline produced is preserved
    assert a.completed_at is not None


def test_timed_out_finalization_wakes_enabled_role_once_after_terminal_commit(
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
        enqueue_rubric_retry_on_commit=True,
    ):
        assert wake_agent_on_commit is False
        assert enqueue_rubric_retry_on_commit is False
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.taali_score = 8.4
        _db.commit()
        return {"ok": True}

    wake_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(assessments_svc, "submit_assessment", fake_submit)
    monkeypatch.setattr(
        agent_tasks.agent_cohort_tick_role,
        "delay",
        lambda role_id, *, activation: wake_calls.append((role_id, activation)),
    )

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "finalized"
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert wake_calls == [(role.id, False)]


def test_finalize_survives_scoring_failure(client, db, monkeypatch):
    """Sandbox gone / Anthropic error: the row must still end terminal + visible
    (flagged for rescore), never left IN_PROGRESS to be discarded."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(client, db, headers, task["id"],
                         status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=40)

    def boom(*_args, **_kwargs):
        raise RuntimeError("e2b sandbox expired")

    monkeypatch.setattr(assessments_svc, "submit_assessment", boom)

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "finalized"
    assert result["scoring_failed"] is True
    db.refresh(a)
    assert a.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert a.scoring_failed is True
    assert a.completed_at is not None


def test_finalize_yields_to_racing_candidate_submit(client, db, monkeypatch):
    """If the candidate's own submit won the atomic claim (409), don't relabel it
    as a timeout completion."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _make_assessment(client, db, headers, task["id"],
                         status=AssessmentStatus.IN_PROGRESS, started_minutes_ago=40)

    def already_submitted(*_args, **_kwargs):
        raise HTTPException(status_code=409, detail="Assessment already submitted")

    monkeypatch.setattr(assessments_svc, "submit_assessment", already_submitted)

    result = assessments_svc.finalize_timed_out_assessment(a, db)

    assert result["status"] == "already_submitted"
    db.refresh(a)
    assert a.completed_due_to_timeout in (False, None)
    assert a.status != AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT


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
    ):
        assert wake_agent_on_commit is False
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        _db.commit()
        return {"ok": True}

    monkeypatch.setattr(assessments_svc, "submit_assessment", fake_submit)

    summary = finalize_timed_out_assessments()

    assert summary["finalized"] == 1
    assert summary["skipped"] == 1
    db.expire_all()
    assert db.get(Assessment,expired.id).status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert db.get(Assessment,active.id).status == AssessmentStatus.IN_PROGRESS
