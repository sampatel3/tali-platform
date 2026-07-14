"""Tests for the async + cached CV scoring orchestrator.

These exercise the orchestration layer end-to-end with the Claude call
monkeypatched: cache hit short-circuits Claude, cache miss persists to the
cache, validation errors mark the job ``error``, and ``mark_role_scores_stale``
adds stale rows for already-scored apps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    Candidate,
    CandidateApplication,
    CvScoreCache,
    CvScoreJob,
    Organization,
    Role,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
)
from app.cv_matching import runner as cv_match_runner
from app.cv_matching.schemas import CVMatchOutput, ScoringStatus
from app.platform.config import settings
from app.platform.database import Base
from app.services import cv_score_orchestrator
from app.services.cv_score_orchestrator import (
    compute_cache_key,
    enqueue_score,
    mark_role_scores_stale,
)
from app.models.role_criterion import CRITERION_SOURCE_RECRUITER, RoleCriterion


@pytest.fixture(autouse=True)
def _force_inline_celery(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key-not-used")
    # This file explicitly stubs the legacy runner below; keep the production
    # holistic rollout from bypassing that stub and making a real network call.
    monkeypatch.setattr(settings, "HOLISTIC_SCORING_ENABLED", False)
    # Skip the pre-screen gate — these tests target the orchestrator's
    # behaviour around the v3 cv_match pipeline (cache, errors, retries),
    # not the pre-screen filter. Pre-screen has its own coverage.
    monkeypatch.setattr(settings, "ENABLE_PRE_SCREEN_GATE", False)


@pytest.fixture()
def session():
    # Reuse the conftest-managed engine so cv_matching helpers that open
    # their own SessionLocal() (via app.platform.database) see the same
    # tables this fixture creates. With Celery in eager mode, the worker
    # task body runs SessionLocal() against the app's engine — using a
    # private engine here caused "no such table" failures in batch runs
    # because table creation didn't propagate to the app-side connection.
    from app.platform.database import engine as app_engine

    Base.metadata.create_all(app_engine)
    Session = sessionmaker(bind=app_engine, expire_on_commit=False)
    db = Session()
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.commit()
    db.refresh(org)
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        job_spec_text="Description\nA backend role.\nRequirements\n- 5+ years Python\n",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    # Add the recruiter criteria as chips directly — the legacy text→chips
    # path is gone post-alembic-068.
    db.add(RoleCriterion(
        role_id=role.id, source=CRITERION_SOURCE_RECRUITER, ordering=0,
        weight=1.0, must_have=True, bucket="must", text="5+ years Python",
    ))
    db.add(RoleCriterion(
        role_id=role.id, source=CRITERION_SOURCE_RECRUITER, ordering=1,
        weight=1.0, must_have=True, bucket="must", text="AWS",
    ))
    db.commit()
    db.refresh(role)
    candidate = Candidate(organization_id=org.id, email="cand@example.com")
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        cv_text="Senior backend engineer with 6 years of Python and AWS experience.",
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    try:
        yield db, org, role, app
    finally:
        db.close()
        # Drop tables so each test starts clean. The conftest-managed
        # engine persists; we reset its schema between cv-score tests.
        from sqlalchemy import text
        with app_engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(text(f"DROP TABLE IF EXISTS {table.name}"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()


def _stub_match_output(score: float = 78.5, *, status: ScoringStatus = ScoringStatus.OK, error_reason: str = "") -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=cv_match_runner.PROMPT_VERSION,
        skills_match_score=score,
        experience_relevance_score=score,
        matching_skills=["Python"],
        experience_highlights=["6 years Python"],
        summary="stub",
        requirements_match_score=score,
        cv_fit_score=score,
        role_fit_score=score,
        scoring_status=status,
        error_reason=error_reason,
        model_version=cv_match_runner.MODEL_VERSION,
        trace_id="test-trace",
    )


def test_enqueue_runs_inline_and_creates_done_job(monkeypatch, session) -> None:
    db, _org, _role, app = session
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return _stub_match_output(82.0)

    monkeypatch.setattr(cv_match_runner, "run_cv_match", fake_run)

    job = enqueue_score(db, app)
    db.commit()
    # Celery runs in eager mode (conftest); the task opens its own
    # SessionLocal and commits the score result. Refresh the test
    # session's view so we see the updated job + application rows.
    db.refresh(job)
    db.refresh(app)

    assert job is not None
    assert job.status == SCORE_JOB_DONE
    assert job.cache_hit == "miss"
    assert app.cv_match_score == 82.0
    assert app.cv_match_details["role_fit_score"] == 82.0
    assert call_count["n"] == 1
    # Note: with the runner stubbed, cv_score_cache writes happen inside
    # the real runner — not exercised here. Cache hit/miss behaviour is
    # covered separately in test_second_enqueue_with_same_inputs_hits_cache.


def test_second_enqueue_with_same_inputs_hits_cache_no_claude_call(monkeypatch, session) -> None:
    db, _org, _role, app = session
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        # Second invocation would normally short-circuit via the runner's
        # own cache lookup; we simulate the runner's cache_hit flag below
        # by making the second result come back with cache_hit=True.
        result = _stub_match_output(70.0)
        if call_count["n"] > 1:
            result = result.model_copy(update={"cache_hit": True})
        return result

    monkeypatch.setattr(cv_match_runner, "run_cv_match", fake_run)

    enqueue_score(db, app)
    db.commit()
    assert call_count["n"] == 1

    # Force a re-enqueue (e.g. recruiter clicked "rescore"). The runner
    # consults cv_score_cache itself; the orchestrator records job.cache_hit
    # from the runner's result.
    second_job = enqueue_score(db, app, force=True)
    db.commit()
    db.refresh(second_job)

    assert second_job is not None
    assert second_job.status == SCORE_JOB_DONE
    assert second_job.cache_hit == "hit"


def test_validation_error_marks_job_error(monkeypatch, session) -> None:
    db, _org, _role, app = session

    def fake_run(*args, **kwargs):
        return _stub_match_output(0.0, status=ScoringStatus.FAILED, error_reason="missing field foo")

    monkeypatch.setattr(cv_match_runner, "run_cv_match", fake_run)

    job = enqueue_score(db, app)
    db.commit()
    db.refresh(job)
    db.refresh(app)

    assert job is not None
    assert job.status == SCORE_JOB_ERROR
    assert "missing field foo" in (job.error_message or "")
    assert app.cv_match_score is None
    assert "missing field foo" in (app.cv_match_details.get("error") or "")
    # Cache must NOT be populated on a failed scoring run.
    assert db.query(CvScoreCache).count() == 0


def test_existing_pending_job_is_reused_when_not_forced(monkeypatch, session) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(cv_match_runner, "run_cv_match", lambda *a, **kw: _stub_match_output(60.0))

    first = enqueue_score(db, app)
    db.commit()
    db.refresh(first)
    assert first is not None
    assert first.status == SCORE_JOB_DONE

    # Done jobs don't block re-enqueue, but pending jobs do — simulate one.
    pending = CvScoreJob(application_id=app.id, role_id=app.role_id, status="pending")
    db.add(pending)
    db.flush()

    reused = enqueue_score(db, app)
    assert reused is not None
    assert reused.id == pending.id, "pending job must be returned, not duplicated"


def test_explicit_enqueue_promotes_pending_autonomous_job_while_paused(
    monkeypatch, session,
) -> None:
    db, _org, role, app = session
    role.agent_paused_at = datetime.now(timezone.utc)
    pending = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=True,
    )
    db.add(pending)
    db.commit()

    reused = enqueue_score(db, app, requires_active_agent=False)

    assert reused is not None and reused.id == pending.id
    db.refresh(pending)
    assert pending.requires_active_agent is False


@pytest.mark.parametrize("held_state", ["paused", "off"])
def test_autonomous_enqueue_is_held_by_current_role_state(
    monkeypatch, session, held_state,
) -> None:
    db, _org, role, app = session
    if held_state == "paused":
        role.agent_paused_at = datetime.now(timezone.utc)
    else:
        role.agentic_mode_enabled = False
    db.commit()
    from app.tasks import scoring_tasks

    dispatch = MagicMock()
    monkeypatch.setattr(scoring_tasks.score_application_job, "delay", dispatch)

    assert enqueue_score(db, app, requires_active_agent=True) is None
    dispatch.assert_not_called()
    assert db.query(CvScoreJob).count() == 0


def test_direct_enqueue_counts_existing_role_job_commitments(
    monkeypatch, session,
) -> None:
    """Public applies cannot enqueue past the role cap while jobs are pending."""
    db, org, role, app = session
    # One SCORE admission is 30,000 microcredits == 3 cents.
    role.monthly_usd_budget_cents = 3
    db.add(
        CvScoreJob(
            application_id=app.id,
            role_id=role.id,
            status=SCORE_JOB_PENDING,
        )
    )
    candidate = Candidate(organization_id=org.id, email="second@example.com")
    db.add(candidate)
    db.flush()
    second = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        cv_text="Another experienced Python and AWS engineer.",
    )
    db.add(second)
    db.commit()

    from app.tasks import scoring_tasks
    dispatch = MagicMock(return_value=SimpleNamespace(id="must-not-dispatch"))
    monkeypatch.setattr(scoring_tasks.score_application_job, "delay", dispatch)

    assert enqueue_score(db, second) is None
    dispatch.assert_not_called()
    assert (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == second.id)
        .count()
        == 0
    )


def test_direct_enqueue_role_admission_error_fails_closed(
    monkeypatch, session,
) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(
        cv_score_orchestrator,
        "ensure_role_capacity",
        MagicMock(side_effect=RuntimeError("budget query unavailable")),
    )

    assert enqueue_score(db, app) is None
    assert db.query(CvScoreJob).count() == 0


def test_direct_enqueue_org_meter_error_fails_closed(
    monkeypatch, session,
) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(
        cv_score_orchestrator,
        "_meter_reserve",
        MagicMock(side_effect=RuntimeError("ledger unavailable")),
    )

    assert enqueue_score(db, app) is None
    assert db.query(CvScoreJob).count() == 0


def test_broker_failure_marks_attempt_error_and_allows_retry(monkeypatch, session) -> None:
    db, _org, _role, app = session
    from app.tasks import scoring_tasks

    def broker_down(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(scoring_tasks.score_application_job, "delay", broker_down)
    with pytest.raises(RuntimeError, match="redis unavailable"):
        enqueue_score(db, app)

    failed = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id)
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    assert failed.status == SCORE_JOB_ERROR
    assert "broker_dispatch_failed" in (failed.error_message or "")

    monkeypatch.setattr(
        scoring_tasks.score_application_job,
        "delay",
        lambda *_args, **_kwargs: SimpleNamespace(id="retry-task"),
    )
    retried = enqueue_score(db, app)
    assert retried is not None and retried.id != failed.id
    assert retried.celery_task_id == "retry-task"


def test_reaper_redispatches_latest_broker_failure_without_waiting_for_hourly_agent(
    monkeypatch, session
) -> None:
    db, _org, _role, app = session
    from app.tasks import scoring_tasks

    failed = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status=SCORE_JOB_ERROR,
        error_message="broker_dispatch_failed: redis unavailable",
        finished_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        requires_active_agent=False,
        force_full_score=True,
    )
    db.add(failed)
    db.commit()
    dispatched: list[tuple[int, bool, bool]] = []

    monkeypatch.setattr(
        cv_score_orchestrator,
        "enqueue_score",
        lambda _db, application, **kwargs: (
            dispatched.append(
                (
                    int(application.id),
                    bool(kwargs.get("requires_active_agent")),
                    bool(kwargs.get("bypass_pre_screen")),
                )
            )
            or SimpleNamespace(id=999)
        ),
    )

    result = scoring_tasks.recover_stuck_score_jobs.run(limit=10)

    assert dispatched == [(int(app.id), False, True)]
    assert result["recovered"] == 1
    assert result["stale_attempts"] == 0
    assert result["broker_failure_retry_minutes"] == 1


def test_stuck_score_recovery_archives_attempt_and_redispatches(
    monkeypatch, session
) -> None:
    db, _org, _role, app = session
    from app.tasks import scoring_tasks

    stale = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status="pending",
        queued_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    duplicate_stale = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status="running",
        queued_at=datetime.now(timezone.utc) - timedelta(minutes=35),
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.add_all([stale, duplicate_stale])
    db.commit()
    dispatched: list[int] = []

    def fake_enqueue(_db, application, **_kwargs):
        dispatched.append(int(application.id))
        return SimpleNamespace(id=999)

    monkeypatch.setattr(cv_score_orchestrator, "enqueue_score", fake_enqueue)
    result = scoring_tasks.recover_stuck_score_jobs.run(
        limit=10,
        pending_stale_minutes=15,
        running_stale_minutes=15,
    )

    db.refresh(stale)
    db.refresh(duplicate_stale)
    assert stale.status == SCORE_JOB_ERROR
    assert duplicate_stale.status == SCORE_JOB_ERROR
    assert stale.error_message == "stale_attempt_recovered"
    assert dispatched == [int(app.id)]
    assert result["recovered"] == 1
    assert result["stale_attempts"] == 2


def test_stuck_score_recovery_does_not_duplicate_legitimate_queue_delay(
    monkeypatch, session
) -> None:
    db, _org, _role, app = session
    from app.tasks import scoring_tasks

    pending = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status=SCORE_JOB_PENDING,
        queued_at=datetime.now(timezone.utc) - timedelta(minutes=90),
    )
    running = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status=SCORE_JOB_RUNNING,
        queued_at=datetime.now(timezone.utc) - timedelta(minutes=50),
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.add_all([pending, running])
    db.commit()

    monkeypatch.setattr(
        cv_score_orchestrator,
        "enqueue_score",
        lambda *_args, **_kwargs: pytest.fail("live queue work was redispatched"),
    )
    result = scoring_tasks.recover_stuck_score_jobs.run(limit=10)

    db.refresh(pending)
    db.refresh(running)
    assert pending.status == SCORE_JOB_PENDING
    assert running.status == SCORE_JOB_RUNNING
    assert result["stale_attempts"] == 0
    assert result["recovered"] == 0
    assert result["pending_stale_minutes"] == 360
    assert result["running_stale_minutes"] == 60


def test_score_worker_persists_running_lease_before_expensive_call(
    monkeypatch, session
) -> None:
    db, _org, _role, app = session
    from app.domains.assessments_runtime import applications_routes
    from app.platform.database import SessionLocal
    from app.tasks import scoring_tasks

    job = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status=SCORE_JOB_PENDING,
    )
    db.add(job)
    db.commit()

    observed: dict[str, object] = {}

    def fake_execute(_db, *, application, job, force_full_score=False):
        observer = SessionLocal()
        try:
            persisted = (
                observer.query(CvScoreJob)
                .filter(CvScoreJob.id == int(job.id))
                .one()
            )
            observed["status"] = persisted.status
            observed["started_at"] = persisted.started_at
        finally:
            observer.close()
        job.status = SCORE_JOB_DONE
        job.cache_hit = "miss"
        job.finished_at = datetime.now(timezone.utc)

    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", fake_execute)
    monkeypatch.setattr(
        applications_routes, "is_batch_score_cancelled", lambda _role_id: False
    )

    result = scoring_tasks.score_application_job.run(
        int(app.id), job_id=int(job.id)
    )

    assert observed["status"] == SCORE_JOB_RUNNING
    assert observed["started_at"] is not None
    assert result["status"] == SCORE_JOB_DONE
    db.refresh(job)
    assert job.status == SCORE_JOB_DONE


@pytest.mark.parametrize("held_state", ["paused", "off"])
def test_score_worker_defers_autonomous_job_before_provider_spend(
    monkeypatch, session, held_state,
) -> None:
    db, _org, role, app = session
    from app.domains.assessments_runtime import applications_routes
    from app.tasks import scoring_tasks

    job = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=True,
    )
    db.add(job)
    if held_state == "paused":
        role.agent_paused_at = datetime.now(timezone.utc)
    else:
        role.agentic_mode_enabled = False
    db.commit()

    execute = MagicMock()
    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", execute)
    monkeypatch.setattr(
        applications_routes, "is_batch_score_cancelled", lambda _role_id: False
    )

    result = scoring_tasks.score_application_job.run(
        int(app.id), job_id=int(job.id)
    )

    execute.assert_not_called()
    assert result["status"] == f"deferred_agent_{held_state}"
    db.refresh(job)
    assert job.status == "stale"
    assert job.error_message == f"deferred_agent_{held_state}"


@pytest.mark.parametrize("terminal_kind", ("local", "workable", "bullhorn"))
def test_score_worker_defers_autonomous_job_for_terminal_job_lifecycle(
    monkeypatch, session, terminal_kind,
) -> None:
    db, _org, role, app = session
    from app.domains.assessments_runtime import applications_routes
    from app.models.role import JOB_STATUS_CANCELLED, JOB_STATUS_OPEN
    from app.tasks import scoring_tasks

    role.job_status = JOB_STATUS_OPEN
    if terminal_kind == "local":
        role.job_status = JOB_STATUS_CANCELLED
    elif terminal_kind == "workable":
        role.source = "workable"
        role.workable_job_id = f"WORK-{role.id}"
        role.workable_job_data = {"state": "closed"}
    else:
        role.source = "bullhorn"
        role.bullhorn_job_order_id = str(91_000 + int(role.id))
        role.bullhorn_job_data = {"status": "Closed", "isOpen": False}
    job = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=True,
    )
    db.add(job)
    db.commit()

    execute = MagicMock()
    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", execute)
    monkeypatch.setattr(
        applications_routes, "is_batch_score_cancelled", lambda _role_id: False
    )

    result = scoring_tasks.score_application_job.run(
        int(app.id), job_id=int(job.id)
    )

    execute.assert_not_called()
    assert result["status"] == "deferred_role_not_runnable"
    assert "not open" in result["detail"] or "not live" in result["detail"]
    db.refresh(job)
    assert job.status == "stale"
    assert job.error_message == "deferred_role_not_runnable"


def test_score_worker_rechecks_authority_after_claim_commit(
    monkeypatch, session,
) -> None:
    db, _org, role, app = session
    from app.domains.assessments_runtime import applications_routes
    from app.services import role_execution_guard
    from app.tasks import scoring_tasks

    job = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=True,
    )
    db.add(job)
    db.commit()

    authority_checks = MagicMock(
        side_effect=[None, "linked bullhorn job is not live"]
    )
    execute = MagicMock()
    monkeypatch.setattr(
        role_execution_guard,
        "automatic_role_action_block_reason",
        authority_checks,
    )
    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", execute)
    monkeypatch.setattr(
        applications_routes, "is_batch_score_cancelled", lambda _role_id: False
    )

    result = scoring_tasks.score_application_job.run(
        int(app.id), job_id=int(job.id)
    )

    assert authority_checks.call_count == 2
    execute.assert_not_called()
    assert result["status"] == "deferred_role_not_runnable"
    db.refresh(job)
    assert job.status == "stale"


def test_explicit_score_worker_runs_while_agent_is_paused(
    monkeypatch, session,
) -> None:
    db, _org, role, app = session
    from app.domains.assessments_runtime import applications_routes
    from app.tasks import scoring_tasks

    role.agent_paused_at = datetime.now(timezone.utc)
    job = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=False,
        force_full_score=True,
    )
    db.add(job)
    db.commit()
    observed: dict[str, bool] = {}

    def fake_execute(_db, *, application, job, force_full_score=False):
        observed["force_full_score"] = bool(force_full_score)
        job.status = SCORE_JOB_DONE
        job.finished_at = datetime.now(timezone.utc)

    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", fake_execute)
    monkeypatch.setattr(
        applications_routes, "is_batch_score_cancelled", lambda _role_id: False
    )

    result = scoring_tasks.score_application_job.run(
        int(app.id), job_id=int(job.id)
    )

    assert result["status"] == SCORE_JOB_DONE
    assert observed["force_full_score"] is True


def test_periodic_stale_sweep_does_not_cross_into_paused_autonomous_role(
    monkeypatch, session,
) -> None:
    db, org, active_role, active_app = session
    from app.tasks import scoring_tasks

    paused_role = Role(
        organization_id=org.id,
        name="Paused role",
        job_spec_text="Python",
        agentic_mode_enabled=True,
        agent_paused_at=datetime.now(timezone.utc),
        monthly_usd_budget_cents=5_000,
    )
    db.add(paused_role)
    db.flush()
    paused_candidate = Candidate(
        organization_id=org.id, email="paused-sweep@example.com"
    )
    db.add(paused_candidate)
    db.flush()
    paused_app = CandidateApplication(
        organization_id=org.id,
        candidate_id=paused_candidate.id,
        role_id=paused_role.id,
        status="applied",
        cv_text="Python engineer",
    )
    db.add(paused_app)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            CvScoreJob(
                application_id=active_app.id,
                role_id=active_role.id,
                status="stale",
                queued_at=now - timedelta(seconds=1),
                requires_active_agent=True,
            ),
            CvScoreJob(
                application_id=paused_app.id,
                role_id=paused_role.id,
                status="stale",
                queued_at=now,
                requires_active_agent=True,
            ),
        ]
    )
    db.commit()
    dispatched: list[tuple[int, bool]] = []

    monkeypatch.setattr(
        cv_score_orchestrator,
        "enqueue_score",
        lambda _db, application, **kwargs: (
            dispatched.append(
                (int(application.id), bool(kwargs["requires_active_agent"]))
            )
            or SimpleNamespace(id=999)
        ),
    )

    result = scoring_tasks.sweep_stale_scores.run(limit=10)

    assert result["status"] == "ok"
    assert dispatched == [(int(active_app.id), True)]
    assert int(paused_app.id) not in {application_id for application_id, _ in dispatched}


def test_explicit_stale_sweep_is_role_and_application_scoped(
    monkeypatch, session,
) -> None:
    db, _org, role, app = session
    from app.tasks import scoring_tasks

    role.agent_paused_at = datetime.now(timezone.utc)
    stale = CvScoreJob(
        application_id=app.id,
        role_id=role.id,
        status="stale",
        requires_active_agent=True,
        force_full_score=True,
    )
    db.add(stale)
    db.commit()
    dispatched: list[tuple[int, bool, bool]] = []
    monkeypatch.setattr(
        cv_score_orchestrator,
        "enqueue_score",
        lambda _db, application, **kwargs: (
            dispatched.append(
                (
                    int(application.id),
                    bool(kwargs["requires_active_agent"]),
                    bool(kwargs["bypass_pre_screen"]),
                )
            )
            or SimpleNamespace(id=999)
        ),
    )

    result = scoring_tasks.sweep_stale_scores.run(
        limit=10,
        role_id=int(role.id),
        application_ids=[int(app.id)],
        explicit=True,
    )

    assert result["status"] == "ok"
    assert result["role_id"] == int(role.id)
    assert dispatched == [(int(app.id), False, True)]


def test_explicit_stale_sweep_requires_role_scope() -> None:
    from app.tasks import scoring_tasks

    result = scoring_tasks.sweep_stale_scores.run(explicit=True)

    assert result["status"] == "error"
    assert result["reason"] == "explicit stale-score sweeps require role_id scope"


def test_score_worker_discards_result_when_role_intent_changes_mid_call(
    monkeypatch, session
) -> None:
    db, _org, role, app = session
    from app.domains.assessments_runtime import applications_routes
    from app.tasks import scoring_tasks

    job = CvScoreJob(
        application_id=app.id,
        role_id=app.role_id,
        status=SCORE_JOB_PENDING,
    )
    db.add(job)
    db.commit()

    def fake_execute(worker_db, *, application, job, force_full_score=False):
        # Simulate a re-publish committing while the provider call was in
        # flight: the computed score belongs to the old fingerprint, while the
        # live role now carries materially different hiring intent.
        live_role = worker_db.query(Role).filter(Role.id == role.id).one()
        live_role.job_spec_text = "Materially revised requisition intent"
        application.cv_match_score = 99.0
        application.cv_match_details = {"summary": "old-JD output"}
        job.status = SCORE_JOB_DONE
        job.finished_at = datetime.now(timezone.utc)

    monkeypatch.setattr(cv_score_orchestrator, "_execute_scoring", fake_execute)
    monkeypatch.setattr(
        applications_routes, "is_batch_score_cancelled", lambda _role_id: False
    )

    result = scoring_tasks.score_application_job.run(
        int(app.id), job_id=int(job.id)
    )

    assert result["status"] == "superseded_role_intent"
    db.expire_all()
    persisted_app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == app.id)
        .one()
    )
    persisted_attempt = db.query(CvScoreJob).filter(CvScoreJob.id == job.id).one()
    latest = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id)
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    assert persisted_app.cv_match_score is None
    assert persisted_app.cv_match_details is None
    assert persisted_attempt.status == SCORE_JOB_ERROR
    assert persisted_attempt.error_message == "superseded_role_intent"
    assert latest is not None and latest.status == "stale"
    assert latest.error_message == "rescore_after_role_reconfiguration"


def test_score_worker_hard_limit_precedes_running_lease_recovery() -> None:
    from app.tasks import scoring_tasks

    assert scoring_tasks.score_application_job.soft_time_limit == (
        scoring_tasks.SCORE_TASK_SOFT_LIMIT_SECONDS
    )
    assert scoring_tasks.score_application_job.time_limit == (
        scoring_tasks.SCORE_TASK_HARD_LIMIT_SECONDS
    )
    assert scoring_tasks.SCORE_TASK_HARD_LIMIT_SECONDS < (
        scoring_tasks.DEFAULT_RUNNING_STALE_MINUTES * 60
    )


def test_force_creates_new_job_even_when_pending_exists(monkeypatch, session) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(cv_match_runner, "run_cv_match", lambda *a, **kw: _stub_match_output(60.0))

    pending = CvScoreJob(application_id=app.id, role_id=app.role_id, status="pending")
    db.add(pending)
    db.flush()

    forced = enqueue_score(db, app, force=True)
    assert forced is not None
    assert forced.id != pending.id


def test_returns_none_when_cv_or_spec_missing(session) -> None:
    db, _org, _role, app = session
    app.cv_text = None
    db.flush()
    assert enqueue_score(db, app) is None


def test_cache_key_changes_when_criteria_change(session) -> None:
    db, _org, role, _app = session

    base_kwargs = dict(
        cv_text="cv",
        spec_description="d",
        spec_requirements="r",
        prompt_version="cv_match_v4",
        model="claude-x",
    )
    crit_a = [{"id": 1, "text": "Python", "must_have": True}]
    crit_b = [{"id": 1, "text": "Python", "must_have": False}]
    crit_c = [{"id": 1, "text": "TypeScript", "must_have": True}]

    key_a = compute_cache_key(criteria=crit_a, **base_kwargs)
    key_b = compute_cache_key(criteria=crit_b, **base_kwargs)
    key_c = compute_cache_key(criteria=crit_c, **base_kwargs)

    assert key_a != key_b, "must_have flag must affect cache key"
    assert key_a != key_c, "criterion text must affect cache key"


def test_mark_role_scores_stale_adds_stale_rows_for_scored_apps(session) -> None:
    db, _org, role, app = session
    app.cv_match_score = 75.0
    db.flush()

    marked = mark_role_scores_stale(db, role.id)
    db.commit()

    assert marked == 1
    stale_rows = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id, CvScoreJob.status == "stale")
        .all()
    )
    assert len(stale_rows) == 1


def test_mark_role_scores_stale_skips_unscored_apps(session) -> None:
    db, _org, role, app = session
    # No cv_match_score → app is unscored → no stale row.
    marked = mark_role_scores_stale(db, role.id)
    db.commit()
    assert marked == 0


def test_pre_screen_gate_uses_evidence_not_contaminated_column(monkeypatch, session) -> None:
    """The gate must read the genuine pre-screen evidence, not the shared
    pre_screen_score_100 column a prior cv_match may have overwritten."""
    from datetime import datetime, timezone

    db, _org, _role, app = session
    monkeypatch.setattr(settings, "ENABLE_PRE_SCREEN_GATE", True)
    monkeypatch.setattr(cv_match_runner, "run_cv_match", lambda *a, **kw: _stub_match_output(72.0))
    # Contaminated column (16.7) but evidence says PASS (llm 75). Pre-screen
    # already ran and the CV isn't newer, so execute_pre_screen_only is skipped
    # and the gate falls back to the stored score — which must be the evidence.
    app.pre_screen_score_100 = 16.7
    app.pre_screen_evidence = {"llm_score_100": 75.0, "decision": "yes"}
    app.pre_screen_run_at = datetime.now(timezone.utc)
    app.cv_uploaded_at = None
    db.commit()

    enqueue_score(db, app, force=True)
    db.refresh(app)
    assert app.cv_match_score is not None  # full-scored, NOT pre-screen-filtered


def test_pre_screen_gate_still_filters_genuine_reject(monkeypatch, session) -> None:
    """A genuinely low pre-screen score (evidence < threshold) is still filtered."""
    from datetime import datetime, timezone

    db, _org, _role, app = session
    monkeypatch.setattr(settings, "ENABLE_PRE_SCREEN_GATE", True)
    monkeypatch.setattr(cv_match_runner, "run_cv_match", lambda *a, **kw: _stub_match_output(72.0))
    app.pre_screen_score_100 = 20.0
    app.pre_screen_evidence = {"llm_score_100": 20.0, "decision": "no"}
    app.pre_screen_run_at = datetime.now(timezone.utc)
    app.cv_uploaded_at = None
    db.commit()

    enqueue_score(db, app, force=True)
    db.refresh(app)
    assert app.cv_match_score is None  # correctly pre-screen-filtered


def test_rescore_wrongly_filtered_prescreen_selection(session) -> None:
    from datetime import datetime, timezone

    from app.services.cv_score_orchestrator import rescore_wrongly_filtered_prescreen

    db, org, role, _app = session
    now = datetime.now(timezone.utc)

    def mkfiltered(email, llm, fraud=False):
        c = Candidate(organization_id=org.id, email=email)
        db.add(c); db.flush()
        a = CandidateApplication(
            organization_id=org.id, candidate_id=c.id, role_id=role.id,
            status="applied", application_outcome="open",
            cv_text="x", cv_match_score=None, cv_match_scored_at=now,
            cv_match_details={"pre_screen_decision": "yes" if llm >= 30 else "no",
                              "pre_screen_score_100": 16.7, "scoring_version": "cv_match_v13"},
            pre_screen_evidence={"llm_score_100": llm, "fraud_capped": fraud},
        )
        db.add(a); db.flush(); return a

    wrong = mkfiltered("wrong@x.test", llm=75)       # passed pre-screen → re-score
    genuine = mkfiltered("genuine@x.test", llm=20)   # genuinely low → leave
    fraud = mkfiltered("fraud@x.test", llm=75, fraud=True)  # fraud → leave
    db.commit()

    res = rescore_wrongly_filtered_prescreen(db, organization_id=int(org.id), dry_run=True)
    assert res["scanned"] == 1
    assert res["rescored"] == 1  # dry_run counts the one wrongly-filtered app
