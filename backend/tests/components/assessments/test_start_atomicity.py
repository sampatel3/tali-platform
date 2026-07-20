from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.components.assessments import service
from app.components.assessments.repository import utcnow
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task


class _Sandbox:
    def __init__(self, sandbox_id: str):
        self.sandbox_id = sandbox_id


class _RecordingE2B:
    created = 0
    connected: list[str] = []
    closed: list[str] = []

    def __init__(self, _api_key: str):
        pass

    @classmethod
    def reset(cls) -> None:
        cls.created = 0
        cls.connected = []
        cls.closed = []

    def create_sandbox(self) -> _Sandbox:
        type(self).created += 1
        return _Sandbox(f"new-sandbox-{type(self).created}")

    def connect_sandbox(self, sandbox_id: str) -> _Sandbox:
        type(self).connected.append(sandbox_id)
        return _Sandbox(sandbox_id)

    def get_sandbox_id(self, sandbox: _Sandbox) -> str:
        return sandbox.sandbox_id

    def close_sandbox(self, sandbox: _Sandbox) -> None:
        type(self).closed.append(sandbox.sandbox_id)


@pytest.fixture
def assessment_record(db) -> tuple[Assessment, Task]:
    org = Organization(
        name="Atomic start org",
        slug="atomic-start-org",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email="atomic-start@example.com",
        full_name="Atomic Start",
        cv_text="Original assessment CV evidence",
    )
    db.add(candidate)
    db.flush()
    task = Task(
        organization_id=org.id,
        name="Atomic start task",
        description="Produce an implementation artifact",
        task_type="debugging",
        difficulty="mid",
        duration_minutes=30,
        starter_code="print('starter')\n",
        test_code="def test_ok(): assert True\n",
        task_key="atomic-start-task",
        role="data_engineer",
        scenario="Repair the pipeline",
        repo_structure={"files": {"answer.py": "# starter\n"}},
        evaluation_rubric={"implementation": {"weight": 1.0}},
        extra_data={},
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token="atomic-start-token",
        status=AssessmentStatus.PENDING,
        duration_minutes=30,
        expires_at=utcnow() + timedelta(days=1),
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment, task


@pytest.fixture(autouse=True)
def isolated_start_runtime(monkeypatch):
    _RecordingE2B.reset()
    monkeypatch.setattr(service.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(service, "E2BService", _RecordingE2B)
    monkeypatch.setattr(service, "resolve_ai_mode", lambda: "claude_cli_terminal")
    monkeypatch.setattr(service, "freeze_assessment_task", lambda *_args: False)
    monkeypatch.setattr(
        service,
        "task_view_for_assessment",
        lambda _assessment, task: task,
    )
    monkeypatch.setattr(service, "_enforce_artifact_first_task", lambda _task: None)
    monkeypatch.setattr(
        service,
        "get_assessment_start_gate",
        lambda *_args, **_kwargs: {
            "can_start": True,
            "reason": None,
            "message": None,
            "organization": None,
        },
    )
    monkeypatch.setattr(
        service,
        "_workspace_repo_root",
        lambda _task: "/workspace/atomic-start-task",
    )


def _assert_start_not_persisted(db, assessment_id: int) -> Assessment:
    db.expire_all()
    row = db.get(Assessment, assessment_id)
    assert row is not None
    assert row.status == AssessmentStatus.PENDING
    assert row.started_at is None
    assert row.e2b_session_id is None
    assert row.credit_consumed_at is None
    assert row.candidate_session_hash is None
    assert row.candidate_session_bound_at is None
    assert row.cv_text_snapshot is None
    return row


def test_provisioning_failure_rolls_back_start_and_retry_uses_fresh_sandbox(
    db,
    assessment_record,
    monkeypatch,
):
    assessment, _task = assessment_record
    # Candidate session binding is flushed by the route before the service is
    # called. It must be part of the same rollback as a failed first start.
    assessment.candidate_session_hash = "candidate-session-digest"
    assessment.candidate_session_bound_at = utcnow()
    db.flush()

    provision_attempts = 0

    def provision(_sandbox, row, _task):
        nonlocal provision_attempts
        provision_attempts += 1
        assert row.status == AssessmentStatus.PENDING
        assert row.started_at is None
        assert row.e2b_session_id is None
        return provision_attempts > 1

    monkeypatch.setattr(service, "_clone_assessment_branch_into_workspace", provision)
    monkeypatch.setattr(
        service,
        "_run_workspace_bootstrap",
        lambda *_args, **_kwargs: {
            "ran": True,
            "success": True,
            "must_succeed": True,
            "working_dir": "/workspace/atomic-start-task",
            "steps": [],
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        service.start_or_resume_assessment(assessment, db)
    assert exc_info.value.status_code == 500
    assert _RecordingE2B.closed == ["new-sandbox-1"]
    row = _assert_start_not_persisted(db, assessment.id)

    result = service.start_or_resume_assessment(row, db)

    db.refresh(row)
    assert result["sandbox_id"] == "new-sandbox-2"
    assert row.status == AssessmentStatus.IN_PROGRESS
    assert row.started_at is not None
    assert row.e2b_session_id == "new-sandbox-2"
    assert row.credit_consumed_at is not None
    assert row.cv_text_snapshot == "Original assessment CV evidence"
    assert provision_attempts == 2
    assert _RecordingE2B.closed == ["new-sandbox-1"]


def test_required_bootstrap_failure_leaves_no_timer_or_session(
    db,
    assessment_record,
    monkeypatch,
):
    assessment, _task = assessment_record
    monkeypatch.setattr(
        service,
        "_clone_assessment_branch_into_workspace",
        lambda *_args: True,
    )

    def failed_bootstrap(*_args, **_kwargs):
        assert assessment.status == AssessmentStatus.PENDING
        assert assessment.started_at is None
        assert assessment.e2b_session_id is None
        return {
            "ran": True,
            "success": False,
            "must_succeed": True,
            "working_dir": "/workspace/atomic-start-task",
            "steps": [{"command": "python3 -I -c 'import pytest'", "success": False}],
        }

    monkeypatch.setattr(service, "_run_workspace_bootstrap", failed_bootstrap)

    with pytest.raises(HTTPException) as exc_info:
        service.start_or_resume_assessment(assessment, db)

    assert exc_info.value.status_code == 500
    assert "prepare assessment workspace" in str(exc_info.value.detail).lower()
    _assert_start_not_persisted(db, assessment.id)
    assert _RecordingE2B.closed == ["new-sandbox-1"]


def test_resume_reconnects_verified_workspace_without_provisioning_or_bootstrap(
    db,
    assessment_record,
    monkeypatch,
):
    assessment, _task = assessment_record
    original_started_at = utcnow() - timedelta(minutes=5)
    assessment.status = AssessmentStatus.IN_PROGRESS
    assessment.started_at = original_started_at
    assessment.e2b_session_id = "candidate-live-sandbox"
    assessment.credit_consumed_at = original_started_at
    db.commit()

    monkeypatch.setattr(service, "_sandbox_workspace_is_ready", lambda *_args: True)
    monkeypatch.setattr(
        service,
        "_clone_assessment_branch_into_workspace",
        lambda *_args: pytest.fail("resume must not provision a baseline"),
    )
    monkeypatch.setattr(
        service,
        "_run_workspace_bootstrap",
        lambda *_args, **_kwargs: pytest.fail("resume must not rerun bootstrap"),
    )
    monkeypatch.setattr(
        service,
        "_read_sandbox_repo_files",
        lambda *_args: {"files": {"answer.py": "candidate work\n"}},
    )

    result = service.start_or_resume_assessment(assessment, db)

    db.refresh(assessment)
    assert _RecordingE2B.created == 0
    assert _RecordingE2B.connected == ["candidate-live-sandbox"]
    assert _RecordingE2B.closed == []
    assert assessment.status == AssessmentStatus.IN_PROGRESS
    assert service.ensure_utc(assessment.started_at) == service.ensure_utc(
        original_started_at
    )
    assert assessment.e2b_session_id == "candidate-live-sandbox"
    assert result["task"]["repo_structure"] == {
        "files": {"answer.py": "candidate work\n"}
    }
