from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.components.assessments import service as assessment_service
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task
from app.tasks.rubric_retry_tasks import _retry_due, retry_incomplete_rubric_scoring


def _assessment(retry):
    return SimpleNamespace(score_breakdown={"rubric_grading": {"retry": retry}})


def test_retry_due_honours_backoff_and_recovers_stale_lease():
    now = datetime.now(timezone.utc)
    assert not _retry_due(
        _assessment({"status": "error", "next_attempt_at": (now + timedelta(minutes=5)).isoformat()}),
        now=now,
    )
    assert _retry_due(
        _assessment({"status": "error", "next_attempt_at": (now - timedelta(seconds=1)).isoformat()}),
        now=now,
    )
    assert not _retry_due(
        _assessment({"status": "running", "claimed_at": (now - timedelta(minutes=5)).isoformat()}),
        now=now,
    )
    assert _retry_due(
        _assessment({"status": "running", "claimed_at": (now - timedelta(hours=2)).isoformat()}),
        now=now,
    )


def _seed_incomplete(db) -> Assessment:
    org = Organization(name="Retry Org", slug=f"retry-org-{id(db)}")
    db.add(org)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"retry-{id(db)}@example.test",
        full_name="Retry Candidate",
    )
    task = Task(
        organization_id=org.id,
        name="Retry Task",
        starter_code="starter",
        evaluation_rubric={"quality": {"weight": 1.0}},
    )
    db.add_all([candidate, task])
    db.flush()
    assessment = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token=f"retry-{id(db)}",
        status=AssessmentStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        scoring_partial=True,
        code_snapshots=[{"final": "submitted"}],
        score_breakdown={
            "rubric_grading": {
                "status": "partial",
                "fully_graded": False,
                "failed_dimension_ids": ["quality"],
                "retry": {"status": "pending", "attempt_count": 0},
            }
        },
    )
    db.add(assessment)
    db.commit()
    return assessment


def test_retry_worker_claims_and_completes_without_manual_rescore(db, monkeypatch):
    assessment = _seed_incomplete(db)
    calls = []
    monkeypatch.setattr(
        assessment_service,
        "resume_code_for_assessment",
        lambda *_args, **_kwargs: "submitted",
    )

    def fake_submit(row, final_code, tab_switch_count, worker_db, **kwargs):
        calls.append((final_code, kwargs))
        row.scoring_partial = False
        row.scoring_failed = False
        row.assessment_score = 84.0
        row.taali_score = 86.0
        worker_db.commit()
        return {"success": True, "grading_status": "complete"}

    monkeypatch.setattr(assessment_service, "submit_assessment", fake_submit)

    result = retry_incomplete_rubric_scoring.run(int(assessment.id))

    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    retry = refreshed.score_breakdown["rubric_grading"]["retry"]
    assert result["status"] == "complete"
    assert retry["status"] == "complete"
    assert retry["attempt_count"] == 1
    assert calls[0][0] == "submitted"
    assert calls[0][1]["retry_scoring"] is True
    assert calls[0][1]["suppress_completion_side_effects"] is False


def test_retry_worker_backoffs_when_grading_remains_partial(db, monkeypatch):
    assessment = _seed_incomplete(db)
    monkeypatch.setattr(
        assessment_service,
        "resume_code_for_assessment",
        lambda *_args, **_kwargs: "submitted",
    )
    monkeypatch.setattr(
        assessment_service,
        "submit_assessment",
        lambda *_args, **_kwargs: {"success": True, "grading_status": "pending"},
    )

    result = retry_incomplete_rubric_scoring.run(int(assessment.id))

    db.expire_all()
    refreshed = db.query(Assessment).filter(Assessment.id == assessment.id).one()
    retry = refreshed.score_breakdown["rubric_grading"]["retry"]
    assert result["status"] == "pending"
    assert retry["status"] == "error"
    assert retry["attempt_count"] == 1
    assert _parse_iso(retry["next_attempt_at"]) > datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
