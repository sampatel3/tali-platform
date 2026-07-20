"""Failures after the terminal submission claim must enter durable recovery."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.components.assessments import service as assessment_service
from app.components.assessments.submission_runtime import (
    _open_submission_sandbox,
    submit_assessment_impl,
)
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task
from app.tasks import rubric_retry_tasks


def _seed(db) -> Assessment:
    org = Organization(name="Post Claim Org", slug=f"post-claim-{id(db)}")
    db.add(org)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"post-claim-{id(db)}@example.test",
        full_name="Post Claim Candidate",
    )
    task = Task(
        organization_id=org.id,
        name="Post Claim Task",
        evaluation_rubric={"quality": {"weight": 1.0}},
    )
    db.add_all([candidate, task])
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token=f"post-claim-{id(db)}",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        assessment_score=91.0,
        taali_score=93.0,
    )
    db.add(assessment)
    db.commit()
    return assessment


@pytest.mark.parametrize(
    ("failure", "expected_error_type"),
    [
        (RuntimeError("E2B reconnect failed after COMPLETED claim"), "RuntimeError"),
        (
            HTTPException(
                status_code=500,
                detail="Failed to push candidate branch updates",
            ),
            "HTTPException",
        ),
    ],
    ids=["e2b-reconnect-after-claim", "github-push-after-claim"],
)
def test_post_claim_runtime_failure_is_flagged_and_enqueued(
    db,
    monkeypatch,
    failure,
    expected_error_type,
):
    assessment = _seed(db)
    dispatched: list[int] = []

    def fail_after_claim(row, *_args, **_kwargs):
        claimed = (
            db.query(Assessment)
            .filter(
                Assessment.id == row.id,
                Assessment.status == AssessmentStatus.IN_PROGRESS,
            )
            .update(
                {Assessment.status: AssessmentStatus.COMPLETED},
                synchronize_session=False,
            )
        )
        assert claimed == 1
        db.commit()
        db.refresh(row)
        raise failure

    monkeypatch.setattr(
        assessment_service,
        "submit_assessment_impl",
        fail_after_claim,
    )
    monkeypatch.setattr(
        rubric_retry_tasks.retry_incomplete_rubric_scoring,
        "delay",
        lambda assessment_id: dispatched.append(int(assessment_id)),
    )

    with pytest.raises(type(failure)):
        assessment_service.submit_assessment(
            assessment,
            "submitted code",
            0,
            db,
        )

    db.expire_all()
    recovered = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert recovered.status == AssessmentStatus.COMPLETED
    assert recovered.scoring_failed is True
    assert recovered.scoring_partial is False
    assert recovered.score is None
    assert recovered.assessment_score is None
    assert recovered.taali_score is None
    assert recovered.scored_at is None
    assert dispatched == [int(recovered.id)]

    failure_meta = recovered.score_breakdown["scoring_failure"]
    assert failure_meta["stage"] == "post_claim_submission"
    assert failure_meta["error_type"] == expected_error_type
    assert failure_meta["status"] == "retrying"
    rubric = recovered.score_breakdown["rubric_grading"]
    assert rubric["status"] == "failed"
    assert rubric["fully_graded"] is False
    assert rubric["failed_dimension_ids"] == ["quality"]
    assert rubric["retry"]["status"] == "pending"
    assert any(
        event.get("type") == "assessment_scoring_failed"
        or event.get("event_type") == "assessment_scoring_failed"
        for event in (recovered.timeline or [])
    )


def test_failed_pre_terminal_capture_keeps_assessment_resumable(db):
    assessment = _seed(db)
    assessment.e2b_session_id = "candidate-session"
    db.commit()

    class _UnavailableE2B:
        def __init__(self, _api_key):
            pass

        def connect_sandbox(self, sandbox_id):
            assert sandbox_id == "candidate-session"
            raise RuntimeError("E2B unavailable before artifact freeze")

    with pytest.raises(RuntimeError, match="E2B unavailable before artifact freeze"):
        submit_assessment_impl(
            assessment,
            "candidate final browser code",
            7,
            db,
            settings_obj=SimpleNamespace(
                MVP_DISABLE_PROCTORING=False,
                E2B_API_KEY="e2b-test",
            ),
            e2b_service_cls=_UnavailableE2B,
            workspace_repo_root_fn=lambda _task: "/workspace/repo",
            collect_git_evidence_fn=lambda _sandbox, _root: {},
        )

    db.expire_all()
    claimed = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert claimed.status == AssessmentStatus.IN_PROGRESS
    assert not claimed.code_snapshots
    assert claimed.submission_artifact is None
    assert claimed.runtime_operation_id is None


def test_failed_capture_never_closes_the_live_candidate_sandbox(db):
    assessment = _seed(db)
    assessment.e2b_session_id = "candidate-session"
    db.commit()
    sandbox = SimpleNamespace(
        run_code=lambda _code: {
            "stdout": '{"files": {}, "error": "capture_failed:src/main.py:OSError"}'
        }
    )
    closed: list[object] = []

    class _CaptureFailureE2B:
        def __init__(self, _api_key):
            pass

        def connect_sandbox(self, sandbox_id):
            assert sandbox_id == "candidate-session"
            return sandbox

        def close_sandbox(self, candidate_sandbox):
            closed.append(candidate_sandbox)

    with pytest.raises(HTTPException) as exc_info:
        submit_assessment_impl(
            assessment,
            "candidate final browser code",
            7,
            db,
            settings_obj=SimpleNamespace(
                MVP_DISABLE_PROCTORING=False,
                E2B_API_KEY="e2b-test",
            ),
            e2b_service_cls=_CaptureFailureE2B,
            workspace_repo_root_fn=lambda _task: "/workspace/repo",
            collect_git_evidence_fn=lambda _sandbox, _root: {},
        )

    assert exc_info.value.status_code == 500
    assert closed == []
    db.expire_all()
    resumable = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert resumable.status == AssessmentStatus.IN_PROGRESS
    assert resumable.submission_artifact is None
    assert resumable.runtime_operation_id is None


def test_deferred_submission_returns_durable_receipt_before_grading(db):
    assessment = _seed(db)
    assessment.e2b_session_id = "candidate-session"
    db.commit()
    sandbox = SimpleNamespace(
        run_code=lambda _code: {
            "stdout": '{"files": {"src/main.py": "candidate work\\n"}, "error": null}'
        }
    )

    closed: list[object] = []

    class _ReceiptOnlyE2B:
        def __init__(self, _api_key):
            pass

        def connect_sandbox(self, sandbox_id):
            assert sandbox_id == "candidate-session"
            return sandbox

        def create_sandbox(self):
            pytest.fail("receipt path must return before grading sandbox creation")

        def close_sandbox(self, candidate_sandbox):
            closed.append(candidate_sandbox)

    result = submit_assessment_impl(
        assessment,
        "candidate final browser code",
        7,
        db,
        settings_obj=SimpleNamespace(
            MVP_DISABLE_PROCTORING=False,
            E2B_API_KEY="e2b-test",
        ),
        e2b_service_cls=_ReceiptOnlyE2B,
        workspace_repo_root_fn=lambda _task: "/workspace/repo",
        collect_git_evidence_fn=lambda _sandbox, _root: {"head_sha": "candidate-head"},
        defer_scoring=True,
    )

    assert result["success"] is True
    assert result["grading_status"] == "pending"
    assert result["score"] is None
    assert closed == [sandbox]
    db.expire_all()
    accepted = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert accepted.status == AssessmentStatus.COMPLETED
    assert accepted.completed_due_to_timeout in (False, None)
    assert accepted.submission_artifact_sha256 == result["artifact_gate"]["artifact_sha256"]
    assert accepted.scoring_partial is True
    assert accepted.scoring_failed is False
    retry = accepted.score_breakdown["rubric_grading"]["retry"]
    assert retry["status"] == "pending"
    assert retry["attempt_count"] == 0


def test_timeout_terminal_status_survives_process_death_after_artifact_commit(db):
    """The immutable artifact and timeout status share one acceptance commit."""
    assessment = _seed(db)
    assessment.e2b_session_id = "candidate-session"
    db.commit()
    sandbox = SimpleNamespace(
        run_code=lambda _code: {
            "stdout": '{"files": {"src/main.py": "candidate work\\n"}, "error": null}'
        }
    )

    class _SimulatedProcessDeath(BaseException):
        pass

    class _CrashAfterAcceptanceE2B:
        def __init__(self, _api_key):
            pass

        def connect_sandbox(self, sandbox_id):
            assert sandbox_id == "candidate-session"
            return sandbox

        def close_sandbox(self, candidate_sandbox):
            assert candidate_sandbox is sandbox
            raise _SimulatedProcessDeath("worker died after the acceptance commit")

    with pytest.raises(_SimulatedProcessDeath, match="acceptance commit"):
        submit_assessment_impl(
            assessment,
            "candidate final browser code",
            7,
            db,
            settings_obj=SimpleNamespace(
                MVP_DISABLE_PROCTORING=False,
                E2B_API_KEY="e2b-test",
            ),
            e2b_service_cls=_CrashAfterAcceptanceE2B,
            workspace_repo_root_fn=lambda _task: "/workspace/repo",
            collect_git_evidence_fn=lambda _sandbox, _root: {
                "head_sha": "candidate-head"
            },
            defer_scoring=True,
            completion_status=AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
        )

    db.expire_all()
    accepted = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    original_digest = accepted.submission_artifact_sha256
    assert accepted.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert accepted.status != AssessmentStatus.COMPLETED
    assert accepted.completed_due_to_timeout is True
    assert original_digest
    assert accepted.scoring_partial is True
    assert any(
        event.get("event_type") == "auto_submit_timeout_sweep"
        for event in (accepted.timeline or [])
    )

    retry = assessment_service.finalize_timed_out_assessment(accepted, db)

    assert retry == {
        "status": "skipped",
        "reason": "not_in_progress",
        "assessment_id": accepted.id,
    }
    db.expire_all()
    retried = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    assert retried.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert retried.completed_due_to_timeout is True
    assert retried.submission_artifact_sha256 == original_digest


def test_deferred_submission_uses_existing_retry_task_as_best_effort_kick(
    db,
    monkeypatch,
):
    assessment = _seed(db)
    dispatched: list[int] = []
    monkeypatch.setattr(
        assessment_service,
        "submit_assessment_impl",
        lambda *_args, **_kwargs: {
            "success": True,
            "score": None,
            "grading_status": "pending",
        },
    )
    monkeypatch.setattr(
        rubric_retry_tasks.retry_incomplete_rubric_scoring,
        "delay",
        lambda assessment_id: dispatched.append(int(assessment_id)),
    )

    result = assessment_service.submit_assessment(
        assessment,
        "submitted code",
        0,
        db,
        defer_scoring=True,
    )

    assert result["grading_status"] == "pending"
    assert dispatched == [assessment.id]


def _pushed_retry_assessment(*, pushed_head: str = "candidate-head"):
    return SimpleNamespace(
        id=42,
        e2b_session_id="killed-e2b-session",
        assessment_repo_url="mock://post-claim/repo.git",
        assessment_branch="assessment/42",
        git_evidence={
            "push_returncode": 0,
            "candidate_branch_push_status": "succeeded",
            "candidate_branch": "assessment/42",
            "candidate_branch_head_sha": pushed_head,
        },
        timeline=[],
    )


def test_legacy_retry_without_artifact_fails_closed_after_sandbox_expiry():
    assessment = _pushed_retry_assessment()
    callback_calls = []

    class _KilledE2B:
        def connect_sandbox(self, sandbox_id):
            assert sandbox_id == "killed-e2b-session"
            raise RuntimeError("sandbox was killed after initial submission")

        def create_sandbox(self):
            pytest.fail("runtime must not create an uninitialized retry sandbox")

    e2b = _KilledE2B()
    task = SimpleNamespace(id=9)
    with pytest.raises(RuntimeError, match="immutable submission artifact is unavailable"):
        _open_submission_sandbox(
            e2b,
            assessment,
            task,
            retry_scoring=True,
            recover_retry_sandbox_fn=lambda *_args: callback_calls.append(True),
        )

    assert callback_calls == []


def test_retry_without_push_checkpoint_never_opens_starter_sandbox():
    assessment = _pushed_retry_assessment()
    assessment.git_evidence = {"push_returncode": 0}
    creates = []
    recoveries = []

    class _KilledE2B:
        def connect_sandbox(self, _sandbox_id):
            raise RuntimeError("sandbox was killed")

        def create_sandbox(self):
            creates.append(True)
            return SimpleNamespace(repo_state="starter")

    with pytest.raises(RuntimeError, match="immutable submission artifact is unavailable"):
        _open_submission_sandbox(
            _KilledE2B(),
            assessment,
            SimpleNamespace(id=9),
            retry_scoring=True,
            recover_retry_sandbox_fn=lambda *_args: recoveries.append(True),
        )

    assert creates == []
    assert recoveries == []
