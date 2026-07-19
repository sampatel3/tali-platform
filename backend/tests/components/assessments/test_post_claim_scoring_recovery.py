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
    "failure",
    [
        RuntimeError(
            "E2B reconnect failed at private-e2b.internal api_key=tenant-secret"
        ),
        HTTPException(
            status_code=500,
            detail="Failed to push with Authorization: Bearer tenant-secret",
        ),
    ],
    ids=["e2b-reconnect-after-claim", "github-push-after-claim"],
)
def test_post_claim_runtime_failure_is_flagged_and_enqueued(
    db,
    monkeypatch,
    failure,
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
    assert failure_meta["error_code"] == "submission_pipeline_failed"
    assert failure_meta["status"] == "retrying"
    assert "error" not in failure_meta
    assert "error_type" not in failure_meta
    rubric = recovered.score_breakdown["rubric_grading"]
    assert rubric["status"] == "failed"
    assert rubric["fully_graded"] is False
    assert rubric["failed_dimension_ids"] == ["quality"]
    assert rubric["retry"]["status"] == "pending"
    assert rubric["retry"]["last_error"] == "submission_pipeline_failed"
    assert any(
        event.get("type") == "assessment_scoring_failed"
        or event.get("event_type") == "assessment_scoring_failed"
        for event in (recovered.timeline or [])
    )
    serialized = str(
        {
            "score_breakdown": recovered.score_breakdown,
            "timeline": recovered.timeline,
        }
    )
    assert "tenant-secret" not in serialized
    assert "private-e2b" not in serialized


def test_workspace_bootstrap_persists_stable_infrastructure_error(monkeypatch):
    task = SimpleNamespace(
        id=91,
        extra_data={
            "workspace_bootstrap": {
                "commands": ["python -m pip install -r requirements.txt"],
                "working_dir": "/workspace/repo",
                "must_succeed": True,
            }
        },
    )

    class FakeE2B:
        def run_command(self, *_args, **_kwargs):
            error = RuntimeError(
                "request to private-e2b.internal failed api_key=tenant-secret"
            )
            error.stderr = "Authorization: Bearer tenant-secret"
            raise error

    result = assessment_service._run_workspace_bootstrap(
        FakeE2B(),
        "sandbox",
        task,
        "/workspace/repo",
    )

    assert result["success"] is False
    assert result["steps"][0]["error_code"] == "workspace_command_failed"
    assert result["steps"][0]["stderr_tail"] == ""
    assert "tenant-secret" not in str(result)
    assert "private-e2b" not in str(result)


def test_terminal_claim_persists_final_code_before_sandbox_work(db):
    assessment = _seed(db)

    class _UnavailableE2B:
        def __init__(self, _api_key):
            pass

        def create_sandbox(self):
            raise RuntimeError("E2B unavailable after claim")

    with pytest.raises(RuntimeError, match="E2B unavailable"):
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
    assert claimed.status == AssessmentStatus.COMPLETED
    assert claimed.code_snapshots[-1] == {"final": "candidate final browser code"}
    assert claimed.tab_switch_count == 7


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


def test_killed_retry_recovers_only_through_verified_pushed_branch():
    assessment = _pushed_retry_assessment()
    recovered = SimpleNamespace(repo_state="candidate implementation")
    callback_calls = []

    class _KilledE2B:
        def connect_sandbox(self, sandbox_id):
            assert sandbox_id == "killed-e2b-session"
            raise RuntimeError("sandbox was killed after initial submission")

        def create_sandbox(self):
            pytest.fail("runtime must not create an uninitialized retry sandbox")

    def recover(e2b, row, task):
        callback_calls.append((e2b, row, task))
        return recovered

    e2b = _KilledE2B()
    task = SimpleNamespace(id=9)
    sandbox = _open_submission_sandbox(
        e2b,
        assessment,
        task,
        retry_scoring=True,
        recover_retry_sandbox_fn=recover,
    )

    assert sandbox is recovered
    assert sandbox.repo_state == "candidate implementation"
    assert callback_calls == [(e2b, assessment, task)]


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

    with pytest.raises(RuntimeError, match="no verified candidate branch push"):
        _open_submission_sandbox(
            _KilledE2B(),
            assessment,
            SimpleNamespace(id=9),
            retry_scoring=True,
            recover_retry_sandbox_fn=lambda *_args: recoveries.append(True),
        )

    assert creates == []
    assert recoveries == []


def test_branch_recovery_verifies_exact_candidate_head_before_grading(monkeypatch):
    assessment = _pushed_retry_assessment(pushed_head="candidate-head")
    task = SimpleNamespace(id=9, extra_data={})
    sandbox = SimpleNamespace(repo_state=None)
    closed = []

    class _RecoveryE2B:
        def create_sandbox(self):
            return sandbox

        def close_sandbox(self, value):
            closed.append(value)

    def clone_candidate(value, _assessment, _task):
        value.repo_state = "candidate implementation"
        return True

    monkeypatch.setattr(
        assessment_service,
        "_clone_assessment_branch_into_workspace",
        clone_candidate,
    )
    monkeypatch.setattr(
        assessment_service,
        "_workspace_repo_root",
        lambda _task: "/workspace/repo",
    )
    monkeypatch.setattr(
        assessment_service,
        "_collect_git_evidence_from_sandbox",
        lambda _sandbox, _root: {"head_sha": "candidate-head"},
    )
    monkeypatch.setattr(
        assessment_service,
        "_run_workspace_bootstrap",
        lambda *_args: {
            "ran": True,
            "success": True,
            "must_succeed": True,
        },
    )

    recovered = assessment_service._recover_retry_sandbox_from_pushed_branch(
        _RecoveryE2B(),
        assessment,
        task,
    )

    assert recovered is sandbox
    assert recovered.repo_state == "candidate implementation"
    assert closed == []
    assert assessment.timeline[-1]["event_type"] == "assessment_scoring_sandbox_recovered"
    assert assessment.timeline[-1]["head_sha"] == "candidate-head"


def test_branch_recovery_rejects_starter_head_and_closes_sandbox(monkeypatch):
    assessment = _pushed_retry_assessment(pushed_head="candidate-head")
    task = SimpleNamespace(id=9, extra_data={})
    sandbox = SimpleNamespace(repo_state="starter")
    closed = []

    class _RecoveryE2B:
        def create_sandbox(self):
            return sandbox

        def close_sandbox(self, value):
            closed.append(value)

    monkeypatch.setattr(
        assessment_service,
        "_clone_assessment_branch_into_workspace",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        assessment_service,
        "_workspace_repo_root",
        lambda _task: "/workspace/repo",
    )
    monkeypatch.setattr(
        assessment_service,
        "_collect_git_evidence_from_sandbox",
        lambda _sandbox, _root: {"head_sha": "starter-head"},
    )

    with pytest.raises(RuntimeError, match="does not match submission checkpoint"):
        assessment_service._recover_retry_sandbox_from_pushed_branch(
            _RecoveryE2B(),
            assessment,
            task,
        )

    assert closed == [sandbox]
    assert assessment.timeline == []
