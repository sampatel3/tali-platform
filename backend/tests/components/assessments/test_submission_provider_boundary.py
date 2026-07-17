"""Submission providers must run on detached, drift-checked snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.components.assessments.submission_provider_boundary import (
    finalize_submission_snapshot,
    snapshot_terminal_submission,
)
from app.components.assessments.submission_runtime import submit_assessment_impl
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task


def _seed(db, *, status=AssessmentStatus.IN_PROGRESS) -> Assessment:
    org = Organization(
        name="Detached Submission Org",
        slug=f"detached-submission-{id(db)}",
    )
    db.add(org)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"detached-{id(db)}@example.test",
        full_name="Detached Candidate",
    )
    task = Task(
        organization_id=org.id,
        name="Detached Task",
        task_key=f"detached-task-{id(db)}",
        repo_structure={"name": "detached-repo", "files": {}},
        extra_data={},
    )
    db.add_all([candidate, task])
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token=f"detached-token-{id(db)}",
        status=status,
        started_at=datetime.now(timezone.utc),
        e2b_session_id="detached-sandbox",
        is_demo=True,
        ai_prompts=[],
        code_snapshots=[],
    )
    db.add(assessment)
    db.commit()
    return assessment


def _settings():
    return SimpleNamespace(
        MVP_DISABLE_PROCTORING=False,
        E2B_API_KEY="test-e2b",
        ANTHROPIC_API_KEY="",
        resolved_claude_scoring_model="test-model",
        MVP_DISABLE_WORKABLE=True,
        FRONTEND_URL="https://example.test",
    )


def test_submission_runs_e2b_and_git_collection_without_request_transaction(db):
    assessment = _seed(db)
    observed: list[tuple[str, bool]] = []

    class Sandbox:
        def run_code(self, _code):
            observed.append(("sandbox_run_code", db.in_transaction()))
            return {"stdout": "{}\n"}

    sandbox = Sandbox()

    class E2B:
        def __init__(self, _api_key):
            pass

        def connect_sandbox(self, _sandbox_id):
            observed.append(("connect", db.in_transaction()))
            return sandbox

        def run_tests(self, _sandbox, _test_code):
            observed.append(("tests", db.in_transaction()))
            return {"passed": 1, "failed": 0, "total": 1}

        def close_sandbox(self, _sandbox):
            observed.append(("close", db.in_transaction()))

    def collect(_sandbox, _root):
        observed.append(("git_evidence", db.in_transaction()))
        return {"head_sha": "demo-head", "status_porcelain": ""}

    result = submit_assessment_impl(
        assessment,
        "candidate final code",
        2,
        db,
        settings_obj=_settings(),
        e2b_service_cls=E2B,
        workspace_repo_root_fn=lambda _task: "/workspace/detached-repo",
        collect_git_evidence_fn=collect,
        suppress_completion_side_effects=True,
    )

    assert result["success"] is True
    assert observed
    assert all(in_transaction is False for _, in_transaction in observed)
    db.expire_all()
    stored = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert stored.status == AssessmentStatus.COMPLETED
    assert stored.tests_passed == 1
    assert stored.code_snapshots[-1] == {"final": "candidate final code"}


def test_snapshot_is_column_only_and_releases_transaction(db):
    assessment = _seed(db, status=AssessmentStatus.COMPLETED)

    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=int(assessment.id),
        terminal_statuses={AssessmentStatus.COMPLETED},
    )

    assert db.in_transaction() is False
    assert not hasattr(snapshot.assessment, "_sa_instance_state")
    assert not hasattr(snapshot.task, "_sa_instance_state")
    snapshot.assessment.score = 77.0
    stored = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert stored.score is None


def test_finalization_rejects_task_drift_without_overwriting_scores(db):
    assessment = _seed(db, status=AssessmentStatus.COMPLETED)
    snapshot = snapshot_terminal_submission(
        db,
        assessment_id=int(assessment.id),
        terminal_statuses={AssessmentStatus.COMPLETED},
    )
    snapshot.assessment.score = 8.2
    task = db.query(Task).filter(Task.id == assessment.task_id).one()
    task.name = "Recruiter changed task authority"
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        finalize_submission_snapshot(
            db,
            snapshot,
            terminal_statuses={AssessmentStatus.COMPLETED},
            retry_scoring=False,
            grading_incomplete=False,
            suppress_completion_side_effects=True,
        )

    assert exc_info.value.status_code == 409
    db.rollback()
    stored = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert stored.score is None
