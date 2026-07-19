from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ORG,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import (
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    CvScoreJob,
)
from app.models.organization import Organization
from app.models.role import Role
from app.services import (
    scoring_backfill_recovery,
    scoring_backfill_terminal_reconcile,
)


def _seed_parent(db, *, child_status: str, job_status: str | None):
    token = uuid4().hex
    organization = Organization(name="Parent reconcile", slug=f"parent-{token}")
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=organization.id,
        name="Parent reconcile role",
        source="manual",
        job_spec_text="Score this exact cohort.",
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=organization.id,
        email=f"parent-{token}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="manual",
        cv_text="Exact CV",
    )
    db.add(application)
    db.flush()
    plan = [
        {
            "role_id": int(role.id),
            "role_name": str(role.name),
            "target_application_ids": [int(application.id)],
        }
    ]
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=organization.id,
        organization_id=organization.id,
        status="running",
        counters={
            "backfill_parent": True,
            "include_scored": False,
            "applied_after": None,
            "role_plan_version": (
                scoring_backfill_recovery.SCORING_BACKFILL_PLAN_VERSION
            ),
            "role_plan": plan,
            "role_plan_digest": (
                scoring_backfill_recovery.scoring_backfill_plan_digest(plan)
            ),
            "fanout_cursor": 1,
            "children": [],
            "skipped": [],
            "total_target": 1,
            "fanout_complete": True,
        },
    )
    db.add(parent)
    db.flush()
    child_counters = scoring_backfill_recovery.scoring_backfill_child_counters(
        target_ids=[int(application.id)],
        include_scored=False,
        applied_after=None,
        parent_run_id=int(parent.id),
    )
    child_counters["fanout_complete"] = True
    child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status=child_status,
        counters=child_counters,
        finished_at=(
            datetime.now(timezone.utc)
            if child_status in {"completed", "cancelled", "failed"}
            else None
        ),
        dispatch_key=f"scoring-backfill:{parent.id}:{role.id}",
    )
    db.add(child)
    db.flush()
    if job_status is not None:
        job = CvScoreJob(
            application_id=application.id,
            role_id=role.id,
            batch_run_id=child.id,
            status=job_status,
            cache_hit="miss" if job_status == SCORE_JOB_DONE else None,
            finished_at=(
                None if job_status == SCORE_JOB_PENDING else datetime.now(timezone.utc)
            ),
            requires_active_agent=False,
        )
        db.add(job)
        db.flush()
        persisted_child_counters = dict(child_counters)
        persisted_child_counters.update(
            dispatched_application_ids=[int(application.id)],
            score_job_ids=[int(job.id)],
            owned_score_job_ids=[int(job.id)],
        )
        # Assign a fresh JSON value so SQLAlchemy persists the exact dispatch receipt.
        child.counters = persisted_child_counters
    parent_counters = dict(parent.counters)
    parent_counters["children"] = [
        {
            "role_id": int(role.id),
            "run_id": int(child.id),
            "target": 1,
            "dispatch_status": "dispatched",
        }
    ]
    parent.counters = parent_counters
    db.commit()
    return parent, child


def _bind_reconciler(db, monkeypatch) -> None:
    monkeypatch.setattr(
        scoring_backfill_terminal_reconcile,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
    )


def test_parent_completes_without_status_poll_and_replay_is_idempotent(
    db, monkeypatch
) -> None:
    parent, _child = _seed_parent(
        db,
        child_status="completed",
        job_status=SCORE_JOB_DONE,
    )
    _bind_reconciler(db, monkeypatch)

    first = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )
    second = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert first["completed"] == 1
    assert second["examined"] == second["completed"] == 0
    assert parent.status == "completed"
    assert parent.finished_at is not None
    assert parent.counters["total_scored"] == 1
    assert parent.counters["total_errors"] == 0


def test_parent_waits_for_active_exact_child_receipt(db, monkeypatch) -> None:
    parent, _child = _seed_parent(
        db,
        child_status="running",
        job_status=SCORE_JOB_PENDING,
    )
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["active"] == 1
    assert parent.status == "running"
    assert parent.finished_at is None


def test_parent_fails_closed_when_child_target_linkage_is_not_exact(
    db, monkeypatch
) -> None:
    parent, child = _seed_parent(
        db,
        child_status="completed",
        job_status=SCORE_JOB_DONE,
    )
    child_counters = dict(child.counters)
    child_counters["target_application_ids"] = []
    child.counters = child_counters
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["failed"] == result["invalid"] == 1
    assert parent.status == "failed"
    assert parent.error == "scoring_backfill_terminal_receipts_invalid"
    assert parent.counters["total_errors"] == 1


def test_parent_rejects_overlarge_child_not_enqueued_instead_of_completing(
    db, monkeypatch
) -> None:
    parent, child = _seed_parent(
        db,
        child_status="completed",
        job_status=None,
    )
    child_counters = dict(child.counters)
    child_counters["not_enqueued"] = 2
    child.counters = child_counters
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["failed"] == result["invalid"] == 1
    assert result["completed"] == 0
    assert parent.status == "failed"
    assert parent.error == "scoring_backfill_terminal_receipts_invalid"


@pytest.mark.parametrize(
    ("child_status", "job_status", "expected_status", "errors", "not_processed"),
    (
        ("failed", SCORE_JOB_ERROR, "failed", 1, 0),
        ("completed", None, "failed", 1, 0),
        ("cancelled", None, "cancelled", 0, 1),
    ),
)
def test_parent_propagates_terminal_child_failure_deficit_and_cancellation(
    db,
    monkeypatch,
    child_status,
    job_status,
    expected_status,
    errors,
    not_processed,
) -> None:
    parent, _child = _seed_parent(
        db,
        child_status=child_status,
        job_status=job_status,
    )
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result[expected_status] == 1
    assert parent.status == expected_status
    assert parent.finished_at is not None
    assert parent.counters["total_errors"] == errors
    assert parent.counters["total_not_processed"] == not_processed


def test_parent_reconciliation_honours_terminalization_limit(db, monkeypatch) -> None:
    first_parent, _ = _seed_parent(
        db,
        child_status="completed",
        job_status=SCORE_JOB_DONE,
    )
    second_parent, _ = _seed_parent(
        db,
        child_status="completed",
        job_status=SCORE_JOB_DONE,
    )
    _bind_reconciler(db, monkeypatch)

    first = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=1
    )
    second = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=1
    )

    db.expire_all()
    statuses = {
        db.get(BackgroundJobRun, first_parent.id).status,
        db.get(BackgroundJobRun, second_parent.id).status,
    }
    assert first["completed"] == second["completed"] == 1
    assert statuses == {"completed"}


def test_parent_counts_recruiter_cancelled_job_as_not_processed(
    db, monkeypatch
) -> None:
    parent, child = _seed_parent(
        db,
        child_status="cancelled",
        job_status=SCORE_JOB_ERROR,
    )
    job = db.query(CvScoreJob).filter(CvScoreJob.batch_run_id == child.id).one()
    job.error_message = "cancelled_by_recruiter"
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["cancelled"] == 1
    assert parent.status == "cancelled"
    assert parent.counters["total_errors"] == 0
    assert parent.counters["total_not_processed"] == 1


def test_parent_rejects_terminal_job_identity_outside_child_dispatch_receipt(
    db, monkeypatch
) -> None:
    parent, child = _seed_parent(
        db,
        child_status="completed",
        job_status=SCORE_JOB_DONE,
    )
    candidate = Candidate(
        organization_id=parent.organization_id,
        email=f"rogue-terminal-{parent.id}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=parent.organization_id,
        candidate_id=candidate.id,
        role_id=child.scope_id,
        source="manual",
        cv_text="Not in the immutable child receipt",
    )
    db.add(application)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=application.id,
            role_id=child.scope_id,
            batch_run_id=child.id,
            status=SCORE_JOB_DONE,
            cache_hit="miss",
            finished_at=datetime.now(timezone.utc),
            requires_active_agent=False,
        )
    )
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["failed"] == result["invalid"] == 1
    assert result["completed"] == 0
    assert parent.status == "failed"
    assert parent.error == "scoring_backfill_terminal_receipts_invalid"


def test_parent_rejects_active_job_identity_outside_dispatch_without_waiting(
    db, monkeypatch
) -> None:
    parent, child = _seed_parent(
        db,
        child_status="running",
        job_status=SCORE_JOB_PENDING,
    )
    candidate = Candidate(
        organization_id=parent.organization_id,
        email=f"rogue-active-parent-{parent.id}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=parent.organization_id,
        candidate_id=candidate.id,
        role_id=child.scope_id,
        source="manual",
        cv_text="Not dispatched by this child",
    )
    db.add(application)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=application.id,
            role_id=child.scope_id,
            batch_run_id=child.id,
            status=SCORE_JOB_PENDING,
            requires_active_agent=False,
        )
    )
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents(
        limit=5
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["failed"] == result["invalid"] == 1
    assert result["active"] == 0
    assert parent.status == "failed"
    assert parent.error == "scoring_backfill_terminal_receipts_invalid"
