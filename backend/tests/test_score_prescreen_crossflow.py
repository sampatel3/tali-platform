"""Cross-workflow authority between standalone pre-screen and CV scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.orm import Query, Session

from app.models.background_job_run import (
    JOB_KIND_PRE_SCREEN_BATCH,
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
from app.models.prescreen_batch_item import PrescreenBatchItem
from app.models.role import Role
from app.platform.database import SessionLocal
from app.services import cv_score_orchestrator
from app.services.score_prescreen_authority import (
    SCORE_DEFERRED_PRESCREEN_ERROR,
    SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS,
    SCORE_PRESCREEN_RETRY_SECONDS,
    claim_score_provider_ownership,
)
from app.tasks import scoring_tasks
from app.tasks.prescreen_tasks import pre_screen_application_job


def _application(db, suffix: str) -> tuple[Organization, Role, CandidateApplication]:
    org = Organization(name=f"Crossflow {suffix}", slug=f"crossflow-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name=f"Engineer {suffix}",
        job_spec_text="Build reliable Python services",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"crossflow-{suffix}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        cv_text="Senior Python engineer",
    )
    db.add(application)
    db.flush()
    return org, role, application


def _prescreen_item(
    db,
    *,
    org_id: int,
    role_id: int,
    application_id: int,
    status: str,
    suffix: str,
) -> tuple[BackgroundJobRun, PrescreenBatchItem]:
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=int(role_id),
        organization_id=int(org_id),
        status="running",
        counters={"total": 1, "refresh": False, "suffix": suffix},
    )
    db.add(run)
    db.flush()
    item = PrescreenBatchItem(
        run_id=int(run.id),
        organization_id=int(org_id),
        role_id=int(role_id),
        application_id=int(application_id),
        status=status,
        provider_attempt_token=f"attempt-{suffix}" if status == "attempting" else None,
        provider_attempt_started_at=(
            datetime.now(timezone.utc) if status == "attempting" else None
        ),
    )
    db.add(item)
    db.flush()
    return run, item


def _score_job(db, application: CandidateApplication, *, force: bool = False) -> CvScoreJob:
    job = CvScoreJob(
        application_id=int(application.id),
        role_id=int(application.role_id),
        status=SCORE_JOB_PENDING,
        requires_active_agent=False,
        force_full_score=force,
    )
    db.add(job)
    db.commit()
    return job


def _disable_batch_cancel(monkeypatch) -> None:
    from app.domains.assessments_runtime import applications_routes

    monkeypatch.setattr(
        applications_routes,
        "is_batch_score_cancelled",
        lambda _role_id: False,
    )


def test_prescreen_first_defers_then_same_score_job_completes_without_second_stage1(
    db, monkeypatch
):
    org, role, application = _application(db, "prescreen-first")
    _run, item = _prescreen_item(
        db,
        org_id=int(org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        status="attempting",
        suffix="prescreen-first",
    )
    job = _score_job(db, application)
    _disable_batch_cancel(monkeypatch)
    monkeypatch.setitem(scoring_tasks.celery_app.conf, "task_always_eager", False)
    publishes: list[dict] = []
    monkeypatch.setattr(
        scoring_tasks.score_application_job,
        "apply_async",
        lambda **kwargs: publishes.append(kwargs) or SimpleNamespace(id="retry-1"),
    )
    execute = MagicMock()
    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", execute)

    deferred = scoring_tasks.score_application_job.run(
        int(application.id), job_id=int(job.id)
    )

    assert deferred == {
        "status": SCORE_DEFERRED_PRESCREEN_ERROR,
        "application_id": int(application.id),
        "retry_scheduled": True,
    }
    execute.assert_not_called()
    db.refresh(job)
    assert job.status == SCORE_JOB_PENDING
    assert job.error_message == SCORE_DEFERRED_PRESCREEN_ERROR
    assert job.started_at is None
    assert publishes == [
        {
            "args": (int(application.id),),
            "kwargs": {
                "job_id": int(job.id),
                "force_full_score": False,
                "prescreen_defer_attempt": 1,
            },
            "countdown": SCORE_PRESCREEN_RETRY_SECONDS,
        }
    ]

    item.status = "done"
    item.finished_at = datetime.now(timezone.utc)
    application.pre_screen_run_at = datetime.now(timezone.utc)
    application.genuine_pre_screen_score_100 = 92.0
    db.commit()
    provider_boundary: list[int] = []

    def complete_score(worker_db, *, application, job, force_full_score=False):
        provider_boundary.append(int(application.id))
        job.status = SCORE_JOB_DONE
        job.cache_hit = "miss"
        job.finished_at = datetime.now(timezone.utc)

    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", complete_score)
    completed = scoring_tasks.score_application_job.run(
        int(application.id), job_id=int(job.id), prescreen_defer_attempt=1
    )

    assert completed["status"] == SCORE_JOB_DONE
    assert provider_boundary == [int(application.id)]
    db.refresh(job)
    assert job.status == SCORE_JOB_DONE
    assert job.error_message is None


def test_scoring_first_makes_prescreen_skip_and_releases_app_lock_before_provider(
    db, monkeypatch
):
    org, role, application = _application(db, "score-first")
    _run, item = _prescreen_item(
        db,
        org_id=int(org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        status="queued",
        suffix="score-first",
    )
    job = _score_job(db, application)
    _disable_batch_cancel(monkeypatch)
    stage1 = MagicMock(side_effect=AssertionError("pre-screen provider was called"))
    monkeypatch.setattr(
        "app.services.pre_screening_service.execute_pre_screen_only", stage1
    )
    application_locks: list[object] = []
    transaction_events: list[str] = []
    original_with_for_update = Query.with_for_update
    original_commit = Session.commit

    def track_lock(query, *args, **kwargs):
        if kwargs.get("of") is CandidateApplication:
            application_locks.append(kwargs["of"])
            transaction_events.append("application_lock")
        elif query.column_descriptions[0].get("entity") is Role:
            transaction_events.append("role_lock")
        return original_with_for_update(query, *args, **kwargs)

    def track_commit(session):
        transaction_events.append("commit")
        return original_commit(session)

    monkeypatch.setattr(Query, "with_for_update", track_lock)
    monkeypatch.setattr(Session, "commit", track_commit)
    nested: list[dict] = []

    def score_provider(worker_db, *, application, job, force_full_score=False):
        assert "application_lock" in transaction_events
        assert transaction_events[-2:] == ["role_lock", "commit"]
        nested.append(pre_screen_application_job.run(int(item.id)))
        job.status = SCORE_JOB_DONE
        job.cache_hit = "miss"
        job.finished_at = datetime.now(timezone.utc)

    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", score_provider)
    result = scoring_tasks.score_application_job.run(
        int(application.id), job_id=int(job.id)
    )

    assert result["status"] == SCORE_JOB_DONE
    assert application_locks
    assert nested == [
        {
            "status": "skipped",
            "item_id": int(item.id),
            "application_id": int(application.id),
        }
    ]
    stage1.assert_not_called()
    db.expire_all()
    skipped = db.get(PrescreenBatchItem, int(item.id))
    assert skipped.status == "skipped"
    assert skipped.error_code == "score_job_active"


def test_attempts_from_other_tenant_or_role_do_not_block_score_owner(db, monkeypatch):
    org, role, application = _application(db, "scope-target")
    other_org, other_role, _other_app = _application(db, "scope-other")
    same_org_other_role = Role(
        organization_id=int(org.id),
        name="Different role",
        job_spec_text="Different role intent",
    )
    db.add(same_org_other_role)
    db.flush()
    _prescreen_item(
        db,
        org_id=int(org.id),
        role_id=int(same_org_other_role.id),
        application_id=int(application.id),
        status="attempting",
        suffix="wrong-role",
    )
    _prescreen_item(
        db,
        org_id=int(other_org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        status="attempting",
        suffix="wrong-tenant",
    )
    assert other_role.organization_id == other_org.id
    job = _score_job(db, application)
    _disable_batch_cancel(monkeypatch)
    executed: list[int] = []

    def complete(worker_db, *, application, job, force_full_score=False):
        executed.append(int(application.id))
        job.status = SCORE_JOB_DONE
        job.finished_at = datetime.now(timezone.utc)

    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", complete)
    result = scoring_tasks.score_application_job.run(
        int(application.id), job_id=int(job.id)
    )

    assert result["status"] == SCORE_JOB_DONE
    assert executed == [int(application.id)]


def test_claim_refreshes_application_snapshot_completed_by_prescreen(db):
    org, role, application = _application(db, "fresh-snapshot")
    job = _score_job(db, application)
    worker = SessionLocal()
    writer = SessionLocal()
    try:
        stale_application = worker.get(CandidateApplication, int(application.id))
        assert stale_application.pre_screen_run_at is None
        completed = writer.get(CandidateApplication, int(application.id))
        completed_at = datetime.now(timezone.utc)
        completed.pre_screen_run_at = completed_at
        completed.genuine_pre_screen_score_100 = 88.0
        writer.commit()

        claim = claim_score_provider_ownership(
            worker,
            application_id=int(application.id),
            organization_id=int(org.id),
            role_id=int(role.id),
            job_id=int(job.id),
            claimed_at=datetime.now(timezone.utc),
        )

        assert claim == "claimed"
        assert stale_application.pre_screen_run_at.replace(
            tzinfo=timezone.utc
        ) == completed_at
        assert stale_application.genuine_pre_screen_score_100 == 88.0
    finally:
        worker.rollback()
        writer.close()
        worker.close()


def test_lost_defer_publish_is_recovered_without_six_hour_pending_wait(
    db, monkeypatch
):
    org, role, application = _application(db, "defer-recovery")
    _prescreen_item(
        db,
        org_id=int(org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        status="attempting",
        suffix="defer-recovery",
    )
    job = _score_job(db, application, force=True)
    _disable_batch_cancel(monkeypatch)
    monkeypatch.setitem(scoring_tasks.celery_app.conf, "task_always_eager", False)
    monkeypatch.setattr(
        scoring_tasks.score_application_job,
        "apply_async",
        MagicMock(side_effect=RuntimeError("broker unavailable")),
    )

    deferred = scoring_tasks.score_application_job.run(
        int(application.id), job_id=int(job.id)
    )
    assert deferred["retry_scheduled"] is False
    db.refresh(job)
    assert job.status == SCORE_JOB_PENDING
    assert job.error_message == SCORE_DEFERRED_PRESCREEN_ERROR
    job.queued_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    db.commit()
    redispatched: list[tuple[int, bool, bool]] = []

    def enqueue(_db, app, **kwargs):
        redispatched.append(
            (
                int(app.id),
                bool(kwargs["requires_active_agent"]),
                bool(kwargs["bypass_pre_screen"]),
            )
        )
        return SimpleNamespace(id=999)

    monkeypatch.setattr(cv_score_orchestrator, "enqueue_score", enqueue)
    recovered = scoring_tasks.recover_stuck_score_jobs.run(
        limit=10,
        pending_stale_minutes=360,
        broker_failure_retry_minutes=1,
    )

    assert recovered["stale_attempts"] == 1
    assert recovered["recovered"] == 1
    assert redispatched == [(int(application.id), False, True)]
    db.refresh(job)
    assert job.status == SCORE_JOB_ERROR
    assert job.error_message == "stale_attempt_recovered"


def test_defer_chain_is_bounded_and_leaves_durable_recovery_marker(db, monkeypatch):
    org, role, application = _application(db, "defer-bound")
    _prescreen_item(
        db,
        org_id=int(org.id),
        role_id=int(role.id),
        application_id=int(application.id),
        status="attempting",
        suffix="defer-bound",
    )
    job = _score_job(db, application)
    _disable_batch_cancel(monkeypatch)
    monkeypatch.setitem(scoring_tasks.celery_app.conf, "task_always_eager", False)
    publish = MagicMock()
    monkeypatch.setattr(scoring_tasks.score_application_job, "apply_async", publish)

    result = scoring_tasks.score_application_job.run(
        int(application.id),
        job_id=int(job.id),
        prescreen_defer_attempt=SCORE_PRESCREEN_MAX_DEFER_ATTEMPTS,
    )

    assert result["status"] == SCORE_DEFERRED_PRESCREEN_ERROR
    assert result["retry_scheduled"] is False
    publish.assert_not_called()
    assert scoring_tasks.score_application_job.max_retries == 0
    db.refresh(job)
    assert job.status == SCORE_JOB_PENDING
    assert job.error_message == SCORE_DEFERRED_PRESCREEN_ERROR


def test_defer_recovery_failure_log_drops_private_exception_text(
    db, monkeypatch, caplog
):
    _org, _role, application = _application(db, "recovery-log")
    job = _score_job(db, application)
    job.error_message = SCORE_DEFERRED_PRESCREEN_ERROR
    job.queued_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    db.commit()
    private_marker = "broker-response bearer-private-candidate-data"
    monkeypatch.setattr(
        cv_score_orchestrator,
        "enqueue_score",
        MagicMock(side_effect=RuntimeError(private_marker)),
    )
    caplog.set_level(logging.ERROR, logger="app.tasks.scoring_tasks")

    result = scoring_tasks.recover_stuck_score_jobs.run(
        limit=10,
        pending_stale_minutes=360,
        broker_failure_retry_minutes=1,
    )

    assert result["status"] == "partial"
    assert result["errors"] == 1
    assert "error_type=RuntimeError" in caplog.text
    assert private_marker not in caplog.text
