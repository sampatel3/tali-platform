from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
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
from app.services import scoring_batch_terminal_reconcile
from app.services.scoring_batch_successors import QUEUE_CONTRACT


def _seed_batch(db, *, job_status: str | None):
    organization = Organization(name="Terminal reconcile", slug=f"terminal-{id(db)}")
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=organization.id,
        name="Terminal reconcile",
        source="manual",
        job_spec_text="Reconcile exact work.",
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=organization.id,
        email=f"terminal-{id(db)}@example.test",
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
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={
            "queue_contract": QUEUE_CONTRACT,
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [application.id],
            "dispatched_application_ids": [application.id] if job_status else [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "fanout_complete": True,
        },
    )
    db.add(run)
    db.flush()
    job = None
    if job_status is not None:
        job = CvScoreJob(
            application_id=application.id,
            role_id=role.id,
            batch_run_id=run.id,
            status=job_status,
            finished_at=(
                None if job_status == SCORE_JOB_PENDING else datetime.now(timezone.utc)
            ),
            requires_active_agent=False,
        )
        db.add(job)
        db.flush()
        counters = dict(run.counters)
        counters["score_job_ids"] = [job.id]
        counters["owned_score_job_ids"] = [job.id]
        counters["not_enqueued"] = 0
        run.counters = counters
    db.commit()
    return run, job


def _bind_reconciler(db, monkeypatch) -> None:
    monkeypatch.setattr(
        scoring_batch_terminal_reconcile,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
    )


def test_plain_batch_completes_without_browser_status_poll(db, monkeypatch) -> None:
    run, _job = _seed_batch(db, job_status=SCORE_JOB_DONE)
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["completed"] == 1
    assert run.status == "completed"
    assert run.finished_at is not None
    assert run.counters["scored"] == 1


def test_plain_batch_waits_for_active_exact_receipt(db, monkeypatch) -> None:
    run, _job = _seed_batch(db, job_status=SCORE_JOB_PENDING)
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["active"] == 1
    assert run.status == "running"
    assert run.finished_at is None


def test_plain_batch_terminalizes_drained_receipt_deficit_as_failure(
    db, monkeypatch
) -> None:
    run, _job = _seed_batch(db, job_status=None)
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["failed"] == 1
    assert run.status == "failed"
    assert run.error == "scoring_batch_incomplete_terminal_receipts"
    assert run.finished_at is not None


def test_plain_batch_rejects_overlarge_not_enqueued_instead_of_false_success(
    db, monkeypatch
) -> None:
    run, _job = _seed_batch(db, job_status=None)
    counters = dict(run.counters)
    counters["not_enqueued"] = 2
    run.counters = counters
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["failed"] == 1
    assert result["completed"] == 0
    assert run.status == "failed"
    assert run.error == "scoring_batch_invalid_terminal_receipts"


def test_cancelled_job_receipt_is_not_reported_as_a_scoring_error(
    db, monkeypatch
) -> None:
    run, job = _seed_batch(db, job_status=SCORE_JOB_ERROR)
    assert job is not None
    job.error_message = "cancelled_by_recruiter"
    run.status = "cancelling"
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["cancelled"] == 1
    assert run.status == "cancelled"
    assert run.counters["errors"] == 0
    assert run.counters["not_processed"] == 1


def test_terminal_identity_permutation_fails_closed_despite_matching_counts(
    db, monkeypatch
) -> None:
    run, _job = _seed_batch(db, job_status=None)
    first_application_id = int(run.counters["target_application_ids"][0])
    candidate = Candidate(
        organization_id=run.organization_id,
        email=f"permuted-{run.id}@example.test",
    )
    db.add(candidate)
    db.flush()
    second_application = CandidateApplication(
        organization_id=run.organization_id,
        candidate_id=candidate.id,
        role_id=run.scope_id,
        source="manual",
        cv_text="Wrong terminal identity",
    )
    db.add(second_application)
    db.flush()
    job = CvScoreJob(
        application_id=second_application.id,
        role_id=run.scope_id,
        batch_run_id=run.id,
        status=SCORE_JOB_DONE,
        cache_hit="miss",
        finished_at=datetime.now(timezone.utc),
        requires_active_agent=False,
    )
    db.add(job)
    db.flush()
    counters = dict(run.counters)
    counters.update(
        total=2,
        selected_total=2,
        target_application_ids=sorted(
            [first_application_id, int(second_application.id)]
        ),
        dispatched_application_ids=[first_application_id],
        score_job_ids=[int(job.id)],
        owned_score_job_ids=[int(job.id)],
        not_enqueued=1,
    )
    run.counters = counters
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["failed"] == 1
    assert result["completed"] == 0
    assert run.status == "failed"
    assert run.error == "scoring_batch_invalid_terminal_receipts"


def test_latest_terminal_attempt_supersedes_older_pending_attempt(
    db, monkeypatch
) -> None:
    run, pending_job = _seed_batch(db, job_status=SCORE_JOB_PENDING)
    assert pending_job is not None
    done_job = CvScoreJob(
        application_id=pending_job.application_id,
        role_id=pending_job.role_id,
        batch_run_id=run.id,
        status=SCORE_JOB_DONE,
        cache_hit="miss",
        finished_at=datetime.now(timezone.utc),
        requires_active_agent=False,
    )
    db.add(done_job)
    db.flush()
    counters = dict(run.counters)
    counters["score_job_ids"] = [int(pending_job.id), int(done_job.id)]
    counters["owned_score_job_ids"] = [int(pending_job.id), int(done_job.id)]
    run.counters = counters
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["completed"] == 1
    assert result["active"] == 0
    assert run.status == "completed"
    assert run.counters["scored"] == 1


def test_active_job_identity_outside_dispatch_receipt_fails_without_waiting(
    db, monkeypatch
) -> None:
    run, _job = _seed_batch(db, job_status=None)
    candidate = Candidate(
        organization_id=run.organization_id,
        email=f"rogue-active-{run.id}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=run.organization_id,
        candidate_id=candidate.id,
        role_id=run.scope_id,
        source="manual",
        cv_text="Not dispatched by this run",
    )
    db.add(application)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=application.id,
            role_id=run.scope_id,
            batch_run_id=run.id,
            status=SCORE_JOB_PENDING,
            requires_active_agent=False,
        )
    )
    db.commit()
    _bind_reconciler(db, monkeypatch)

    result = scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches(limit=5)

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["failed"] == 1
    assert result["active"] == 0
    assert run.status == "failed"
    assert run.error == "scoring_batch_invalid_terminal_receipts"
