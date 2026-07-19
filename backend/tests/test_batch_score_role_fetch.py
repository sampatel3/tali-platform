"""Tests for the Celery batch_score_role task: CV fetch + score fan-out.

The bug we're guarding against: ``batch_score_role`` was dropping every
application that didn't already have ``cv_text`` populated, because
``enqueue_score`` returns None for those. In production this meant 599 of
600 candidates got silently skipped after a "Re-score all" click.

These tests exercise the task body with three application states:
- pre-populated cv_text (should enqueue immediately)
- candidate-level cv_text only (should be promoted to application)
- Workable-source missing cv_text (should be fetched via the helper)
"""

from __future__ import annotations


from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Candidate,
    CandidateApplication,
    CvScoreJob,
    Organization,
    Role,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
    SCORE_JOB_RUNNING,
)
from app.models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.platform.config import settings
from app.platform.database import Base
from app.domains.assessments_runtime.scoring_batch_state import (
    scoring_batch_has_active_jobs,
)
from app.services.scoring_batch_fanout_recovery import (
    claim_due_scoring_fanouts,
    mark_scoring_fanout_published,
)
from app.tasks.scoring_batch_recovery_tasks import (
    recover_scoring_batch_dispatches,
)
from app.tasks.scoring_tasks import batch_score_role
from app.tasks.scoring_batch_run import (
    ScoringBatchLeaseLost,
    ScoringBatchProgress,
    claim_scoring_batch_run,
)


@pytest.fixture(autouse=True)
def _force_inline_celery(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")


@pytest.fixture()
def session_factory(monkeypatch):
    """Create an in-memory SQLite + bind both SessionLocal references to it."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.tasks.scoring_tasks.SessionLocal", Session, raising=False)
    monkeypatch.setattr("app.platform.database.SessionLocal", Session, raising=False)
    return Session


def _seed_role_with_apps(Session) -> tuple[int, list[int]]:
    """Create one org/role and three applications in three CV states."""
    db = Session()
    try:
        org = Organization(
            name="Acme",
            slug="acme",
            workable_subdomain="acme",
            workable_access_token="t",
            workable_connected=True,
        )
        db.add(org)
        db.flush()

        role = Role(
            organization_id=org.id,
            name="Engineer",
            job_spec_text="Backend engineer\nRequirements:\n- Python\n",
        )
        db.add(role)
        db.flush()

        # App 1: already has cv_text on the application row
        cand1 = Candidate(organization_id=org.id, email="a@x.com", full_name="A")
        db.add(cand1)
        db.flush()
        app1 = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand1.id,
            role_id=role.id,
            status="applied",
            cv_text="Senior engineer with 7 years Python.",
        )
        db.add(app1)

        # App 2: cv_text only on the candidate row (should be promoted)
        cand2 = Candidate(
            organization_id=org.id,
            email="b@x.com",
            full_name="B",
            cv_text="Backend engineer skilled in Python and AWS.",
            cv_filename="b.pdf",
        )
        db.add(cand2)
        db.flush()
        app2 = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand2.id,
            role_id=role.id,
            status="applied",
        )
        db.add(app2)

        # App 3: Workable-source, no CV anywhere — needs Workable fetch
        cand3 = Candidate(
            organization_id=org.id,
            email="c@x.com",
            full_name="C",
            workable_candidate_id="wk-c",
        )
        db.add(cand3)
        db.flush()
        app3 = CandidateApplication(
            organization_id=org.id,
            candidate_id=cand3.id,
            role_id=role.id,
            status="applied",
            source="workable",
            workable_candidate_id="wk-c",
        )
        db.add(app3)

        db.commit()
        return role.id, [app1.id, app2.id, app3.id]
    finally:
        db.close()


def _seed_run(
    Session, role_id: int, app_ids: list[int], *, status: str = "queued"
) -> int:
    db = Session()
    try:
        role = db.get(Role, role_id)
        run = BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role_id,
            organization_id=role.organization_id,
            status=status,
            counters={
                "total": len(app_ids),
                "selected_total": len(app_ids),
                "target_application_ids": list(app_ids),
                "dispatched_application_ids": [],
                "score_job_ids": [],
                "owned_score_job_ids": [],
                "queue_contract": "background_job_run_successor_v1",
                "include_scored": False,
                "applied_after": None,
                "fanout_complete": False,
            },
        )
        db.add(run)
        db.commit()
        return int(run.id)
    finally:
        db.close()


def test_durable_cancel_database_poll_has_time_and_work_bounds(monkeypatch):

    class _Run:
        status = "running"
        cancel_requested_at = None

    class _Db:
        refreshes = 0

        def refresh(self, run, **_kwargs):
            self.refreshes += 1
            if self.refreshes == 2:
                run.status = "cancelling"

    ticks = iter((100.0, 100.5, 101.0))
    monkeypatch.setattr("app.tasks.scoring_batch_run.monotonic", lambda: next(ticks))
    timed_db, timed_run = _Db(), _Run()
    timed = ScoringBatchProgress(1, 2, False, None)
    assert timed.cancel_requested(timed_db, timed_run) is False
    assert timed.cancel_requested(timed_db, timed_run) is False
    assert timed.cancel_requested(timed_db, timed_run) is True
    assert timed_db.refreshes == 2

    monkeypatch.setattr("app.tasks.scoring_batch_run.monotonic", lambda: 200.0)
    fast_db, fast_run = _Db(), _Run()
    fast = ScoringBatchProgress(1, 2, False, None)
    assert fast.cancel_requested(fast_db, fast_run) is False
    assert all(fast.cancel_requested(fast_db, fast_run) is False for _ in range(9))
    assert fast.cancel_requested(fast_db, fast_run) is True
    assert fast.cancel_requested(fast_db, fast_run) is True
    assert fast_db.refreshes == 2


def test_reused_active_score_job_receipt_counts_once():
    progress = ScoringBatchProgress(1, 2, False, None, total=1)

    progress.record_enqueued(99)
    progress.record_enqueued(99)

    assert progress.enqueued == 1
    assert progress.not_enqueued == 0


def test_batch_score_role_fetches_missing_cvs(session_factory, monkeypatch):
    """All 3 applications should end up with cv_text set + a CvScoreJob created.

    The bug pre-fix: only app1 (which already had cv_text) would be
    counted; apps 2 and 3 silently dropped. After the fix all 3 are
    handled — app2 via candidate promotion, app3 via the Workable fetch
    helper.
    """
    role_id, app_ids = _seed_role_with_apps(session_factory)

    # Stub the Workable fetcher: pretend we successfully fetched and set
    # cv_text on the application row.
    def _fake_workable_fetch(app, candidate, db, org):
        app.cv_text = "Workable-fetched CV text for " + (candidate.full_name or "")
        app.cv_filename = f"{candidate.id}.pdf"
        return True

    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        _fake_workable_fetch,
        raising=False,
    )

    # Stub the per-app score dispatcher so we don't try to call Anthropic.
    # In tests Celery runs in eager mode (conftest.py), so .delay() invokes
    # the score_application_job body in-process, which calls _execute_scoring.
    # Stub _execute_scoring to a no-op success so we don't hit the v3/v4 LLM flow.
    def _fake_execute_scoring(db, *, application, job, **_unused):
        application.cv_match_score = 75.0
        application.cv_match_details = {"summary": "stub"}
        job.status = "done"
        job.cache_hit = "miss"

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        _fake_execute_scoring,
    )

    result = batch_score_role(role_id, include_scored=True)

    assert result == {
        "status": "enqueued",
        "role_id": role_id,
        "count": 3,
        "fetched": 2,
        "fetch_failures": 0,
        "pre_screened_out": 0,
    }

    # Verify state in DB
    db = session_factory()
    try:
        apps = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(app_ids))
            .all()
        )
        for app in apps:
            assert (app.cv_text or "").strip(), f"app {app.id} still missing cv_text"
            assert app.cv_match_score == 75.0
        # 3 done jobs in the audit log
        jobs = db.query(CvScoreJob).filter(CvScoreJob.application_id.in_(app_ids)).all()
        assert len(jobs) == 3
        assert all(j.status == "done" for j in jobs)
    finally:
        db.close()


def test_batch_score_role_skips_when_cv_unfetchable(session_factory, monkeypatch):
    """If Workable fetch fails, the count drops to fetched applications only.

    The application stays without cv_text; enqueue_score returns None
    and the count does NOT include it. This is correct behavior — but
    it's also why the production count was 1: most apps were in this
    state and the helper wasn't even being called.
    """
    role_id, app_ids = _seed_role_with_apps(session_factory)

    # Workable fetch always fails
    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        lambda app, candidate, db, org: False,
        raising=False,
    )

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        lambda db, *, application, job, **_unused: (
            setattr(application, "cv_match_score", 50.0)
            or setattr(job, "status", "done")
        ),
    )

    result = batch_score_role(role_id, include_scored=True)
    # Only app1 (pre-existing cv_text) and app2 (candidate-level promotion) succeed
    assert result == {
        "status": "enqueued",
        "role_id": role_id,
        "count": 2,
        "fetched": 1,
        "fetch_failures": 1,
        "pre_screened_out": 0,
    }


def test_durable_run_reconciles_unenqueued_targets_and_fences_redelivery(
    session_factory, monkeypatch
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        lambda app, candidate, db, org: False,
    )

    def _fake_execute(db, *, application, job, **_unused):
        application.cv_match_score = 70.0
        job.status = "done"

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring", _fake_execute
    )

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result == {
        "status": "enqueued",
        "role_id": role_id,
        "count": 2,
        "total": 3,
        "selected": 3,
        "fetched": 1,
        "fetch_failures": 1,
        "missing_cv": 1,
        "enqueue_skipped": 0,
        "not_enqueued": 1,
        "pre_screened_out": 0,
        "run_id": run_id,
    }
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "running"
        assert run.finished_at is None
        assert run.counters["fanout_state"] == "enqueued"
        assert run.counters["fanout_complete"] is True
        assert run.counters["selected_total"] == 3
        assert run.counters["selected"] == 3
        assert run.counters["target_application_ids"] == sorted(app_ids)
        assert run.counters["not_enqueued"] == 1
        assert run.counters["missing_cv"] == 1
        assert run.counters["fetch_failures"] == 1
        assert db.query(CvScoreJob).count() == 2
    finally:
        db.close()

    duplicate = batch_score_role(role_id, include_scored=True, run_id=run_id)
    assert duplicate["status"] == "already_enqueued"
    assert duplicate["count"] == 2
    db = session_factory()
    try:
        assert db.query(CvScoreJob).count() == 2
    finally:
        db.close()


def test_durable_run_ignores_legacy_role_wide_cancel_flag(session_factory, monkeypatch):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes.is_batch_score_cancelled",
        lambda _role_id: True,
    )
    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        lambda app, _candidate, _db, _org: (
            setattr(app, "cv_text", "Fetched CV") or True
        ),
    )
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        lambda _db, *, application, job, **_unused: (
            setattr(application, "cv_match_score", 70.0)
            or setattr(job, "status", "done")
        ),
    )

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "enqueued"
    assert result["count"] == 3
    assert result["not_enqueued"] == 0
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "running"
        assert run.finished_at is None
        assert run.cancel_requested_at is None
        assert run.counters["fanout_state"] == "enqueued"
        assert run.counters["fanout_complete"] is True
        assert run.counters["selected_total"] == 3
        assert run.counters["not_enqueued"] == 0
        assert db.query(CvScoreJob).count() == 3
    finally:
        db.close()


def test_durable_cancel_receipt_stops_even_without_redis(session_factory):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids, status="cancelling")

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "cancelled"
    assert result["not_enqueued"] == 3
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None
        assert run.cancel_requested_at is not None
        assert run.counters["fanout_state"] == "cancelled_before_fetch"
        assert run.counters["fanout_complete"] is True
        assert db.query(CvScoreJob).count() == 0
    finally:
        db.close()


def test_durable_run_counts_other_enqueue_none_and_finishes_failure(
    session_factory, monkeypatch
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)

    def _fetch(app, candidate, db, org):
        app.cv_text = "Fetched CV"
        return True

    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        _fetch,
    )
    observed_fanout = []

    def _skip(db, *_args, **_kwargs):
        counters = db.get(BackgroundJobRun, run_id).counters
        observed_fanout.append((counters["fanout_state"], counters["fanout_complete"]))
        return None

    monkeypatch.setattr("app.services.cv_score_orchestrator.enqueue_score", _skip)

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "failed"
    assert result["count"] == 0
    assert result["missing_cv"] == 0
    assert result["enqueue_skipped"] == 3
    assert result["not_enqueued"] == 3
    assert observed_fanout == [("enqueuing", False)] * 3
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.error == "scoring_batch_nothing_enqueued"
        assert run.counters["fanout_complete"] is True
        assert run.counters["not_enqueued"] == 3
    finally:
        db.close()


def test_durable_run_rejects_borrowed_receipt_without_enqueuing(
    session_factory,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        run.scope_id = role_id + 1000
        db.commit()
    finally:
        db.close()

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "invalid_run"
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "queued"
        assert "fanout_state" not in run.counters
        assert db.query(CvScoreJob).count() == 0
    finally:
        db.close()


def test_durable_run_terminalizes_missing_role_before_dispatch(session_factory):
    db = session_factory()
    try:
        org = Organization(name="Missing Role Org", slug="missing-role-org")
        db.add(org)
        db.flush()
        run = BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=404,
            organization_id=org.id,
            status="queued",
            counters={"total": 2, "target_application_ids": [12, 11]},
        )
        db.add(run)
        db.commit()
        run_id = int(run.id)
    finally:
        db.close()

    result = batch_score_role(404, run_id=run_id)

    assert result == {"status": "missing_role", "role_id": 404}
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "failed"
        assert run.error == "scoring_batch_role_missing"
        assert run.finished_at is not None
        assert run.counters["fanout_complete"] is True
        assert run.counters["selected_total"] == 2
        assert run.counters["target_application_ids"] == [11, 12]
        assert run.counters["not_enqueued"] == 2
    finally:
        db.close()


def test_durable_exact_snapshot_ignores_malformed_filter_metadata(
    session_factory,
    monkeypatch,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids[:1])
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        counters = dict(run.counters)
        counters["applied_after"] = "not-a-date"
        run.counters = counters
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        lambda _db, *, application, job, **_unused: (
            setattr(application, "cv_match_score", 80.0)
            or setattr(job, "status", "done")
        ),
    )

    result = batch_score_role(
        role_id,
        include_scored=True,
        applied_after="not-a-date",
        run_id=run_id,
    )

    assert result["status"] == "enqueued"
    assert result["count"] == 1
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.error is None
        assert run.counters["target_application_ids"] == app_ids[:1]
        jobs = db.query(CvScoreJob).all()
        assert [job.application_id for job in jobs] == app_ids[:1]
    finally:
        db.close()


def test_legacy_dynamic_batch_rejects_malformed_applied_after(session_factory):
    role_id, _ = _seed_role_with_apps(session_factory)

    with pytest.raises(ValueError):
        batch_score_role(role_id, applied_after="not-a-date")

    db = session_factory()
    try:
        assert db.query(CvScoreJob).count() == 0
    finally:
        db.close()


def test_fanout_failure_does_not_double_reconcile_a_durable_error_job(
    session_factory, monkeypatch
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)

    def _fail_after_receipt(db, app, **_kwargs):
        db.add(
            CvScoreJob(
                application_id=app.id,
                role_id=app.role_id,
                batch_run_id=_kwargs.get("batch_run_id"),
                status="error",
                error_message="broker_dispatch_failed",
                finished_at=datetime.now(timezone.utc),
                requires_active_agent=False,
            )
        )
        db.commit()
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.enqueue_score", _fail_after_receipt
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        batch_score_role(role_id, include_scored=True, run_id=run_id)

    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "failed"
        assert run.counters["fanout_complete"] is True
        assert run.counters["score_job_receipts"] == 1
        assert run.counters["not_enqueued"] == 2
        assert db.query(CvScoreJob).count() == 1
    finally:
        db.close()


def test_durable_target_snapshot_excludes_applications_created_after_post(
    session_factory, monkeypatch
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        counters = dict(run.counters)
        counters.update(
            total=2,
            selected_total=2,
            target_application_ids=app_ids[:2],
        )
        run.counters = counters
        role = db.get(Role, role_id)
        late_candidate = Candidate(
            organization_id=role.organization_id,
            email="late@x.com",
            full_name="Late",
        )
        db.add(late_candidate)
        db.flush()
        late_application = CandidateApplication(
            organization_id=role.organization_id,
            candidate_id=late_candidate.id,
            role_id=role_id,
            status="applied",
            cv_text="Created after the recruiter confirmed the cohort.",
        )
        db.add(late_application)
        db.commit()
        late_application_id = int(late_application.id)
    finally:
        db.close()

    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        lambda _db, *, application, job, **_unused: (
            setattr(application, "cv_match_score", 80.0)
            or setattr(job, "status", "done")
        ),
    )

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["count"] == 2
    db = session_factory()
    try:
        jobs = db.query(CvScoreJob).order_by(CvScoreJob.application_id).all()
        assert [job.application_id for job in jobs] == app_ids[:2]
        assert {job.batch_run_id for job in jobs} == {run_id}
        assert late_application_id not in {job.application_id for job in jobs}
    finally:
        db.close()


def test_expired_fanout_lease_resumes_from_atomic_owned_receipts(
    session_factory, monkeypatch
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        counters = dict(run.counters)
        counters.update(
            fanout_state="enqueuing",
            fanout_complete=False,
            fanout_lease_expires_at=(
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
        )
        run.counters = counters
        first = db.get(CandidateApplication, app_ids[0])
        first.cv_match_score = 77.0
        db.add(
            CvScoreJob(
                application_id=first.id,
                role_id=role_id,
                batch_run_id=run_id,
                status="done",
                finished_at=datetime.now(timezone.utc),
                requires_active_agent=False,
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        lambda app, _candidate, _db, _org: (
            setattr(app, "cv_text", "Fetched CV") or True
        ),
    )
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._execute_scoring",
        lambda _db, *, application, job, **_unused: (
            setattr(application, "cv_match_score", 80.0)
            or setattr(job, "status", "done")
        ),
    )

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["count"] == 3
    db = session_factory()
    try:
        jobs = db.query(CvScoreJob).order_by(CvScoreJob.id).all()
        assert len(jobs) == 3
        assert sorted(job.application_id for job in jobs) == sorted(app_ids)
        assert len({job.application_id for job in jobs}) == 3
        run = db.get(BackgroundJobRun, run_id)
        assert run.counters["fanout_complete"] is True
        assert run.counters["dispatched_application_ids"] == sorted(app_ids)
    finally:
        db.close()


def test_live_fanout_lease_defers_duplicate_delivery_without_new_jobs(
    session_factory,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        counters = dict(run.counters)
        counters.update(
            fanout_state="enqueuing",
            fanout_complete=False,
            fanout_lease_expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat(),
        )
        run.counters = counters
        db.commit()
    finally:
        db.close()

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "delivery_busy"
    assert result["retry_after_seconds"] > 0
    db = session_factory()
    try:
        assert db.query(CvScoreJob).count() == 0
    finally:
        db.close()


def test_expired_delivery_is_fenced_after_a_new_owner_claims_the_run(
    session_factory,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    first_db = session_factory()
    second_db = session_factory()
    try:
        role = first_db.get(Role, role_id)
        first_run, first_result = claim_scoring_batch_run(
            first_db,
            run_id=run_id,
            role_id=role_id,
            organization_id=role.organization_id,
            delivery_id="delivery-a",
        )
        assert first_result is None
        first_claim_token = first_run.counters["fanout_owner_delivery_id"]
        assert first_run.counters["fanout_owner_task_id"] == "delivery-a"
        counters = dict(first_run.counters)
        counters["fanout_lease_expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        first_run.counters = counters
        first_db.commit()

        second_run, second_result = claim_scoring_batch_run(
            second_db,
            run_id=run_id,
            role_id=role_id,
            organization_id=role.organization_id,
            delivery_id="delivery-a",
        )
        assert second_result is None
        second_claim_token = second_run.counters["fanout_owner_delivery_id"]
        assert second_claim_token != first_claim_token
        assert second_run.counters["fanout_owner_task_id"] == "delivery-a"

        stale_progress = ScoringBatchProgress(
            role_id,
            run_id,
            False,
            None,
            owner_delivery_id=first_claim_token,
            total=len(app_ids),
            target_application_ids=list(app_ids),
        )
        with pytest.raises(ScoringBatchLeaseLost):
            stale_progress.save(first_db, first_run, "enqueuing")
        first_db.rollback()

        from app.services.score_job_batch_ownership import (
            claim_live_scoring_batch,
        )

        assert (
            claim_live_scoring_batch(
                first_db,
                batch_run_id=run_id,
                role_id=role_id,
                organization_id=role.organization_id,
                application_id=app_ids[0],
                owner_delivery_id=first_claim_token,
            )
            is None
        )
        first_db.rollback()
        persisted = second_db.get(BackgroundJobRun, run_id)
        second_db.refresh(persisted)
        assert persisted.counters["fanout_owner_delivery_id"] == second_claim_token
        assert persisted.counters["dispatched_application_ids"] == []
    finally:
        first_db.close()
        second_db.close()


def test_batch_ownership_allows_history_but_rejects_overlapping_active_attempts(
    session_factory,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    db = session_factory()
    try:
        first = CvScoreJob(
            application_id=app_ids[0],
            role_id=role_id,
            batch_run_id=run_id,
            status=SCORE_JOB_PENDING,
        )
        db.add(first)
        db.commit()
        db.add(
            CvScoreJob(
                application_id=app_ids[0],
                role_id=role_id,
                batch_run_id=run_id,
                status=SCORE_JOB_RUNNING,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        first.status = SCORE_JOB_ERROR
        first.finished_at = datetime.now(timezone.utc)
        db.add(first)
        db.commit()
        db.add(
            CvScoreJob(
                application_id=app_ids[0],
                role_id=role_id,
                batch_run_id=run_id,
                status=SCORE_JOB_PENDING,
            )
        )
        db.commit()
        assert (
            db.query(CvScoreJob)
            .filter(
                CvScoreJob.batch_run_id == run_id,
                CvScoreJob.application_id == app_ids[0],
            )
            .count()
            == 2
        )
    finally:
        db.close()


def test_batch_ownership_rejects_and_ignores_same_role_non_target(
    session_factory,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids[:2])
    db = session_factory()
    try:
        role = db.get(Role, role_id)
        from app.services.score_job_batch_ownership import (
            claim_live_scoring_batch,
        )

        assert (
            claim_live_scoring_batch(
                db,
                batch_run_id=run_id,
                role_id=role_id,
                organization_id=role.organization_id,
                application_id=app_ids[2],
            )
            is None
        )
        db.add(
            CvScoreJob(
                application_id=app_ids[2],
                role_id=role_id,
                batch_run_id=run_id,
                status=SCORE_JOB_PENDING,
            )
        )
        db.commit()
        run = db.get(BackgroundJobRun, run_id)
        assert not scoring_batch_has_active_jobs(
            db,
            run_id=run_id,
            progress=dict(run.counters),
        )
        owned = CvScoreJob(
            application_id=app_ids[0],
            role_id=role_id,
            batch_run_id=run_id,
            status=SCORE_JOB_PENDING,
        )
        db.add(owned)
        db.commit()
        from app.services.score_job_dispatch import cancel_pending_batch_score_jobs

        assert cancel_pending_batch_score_jobs(db, batch_run_id=run_id) == 1
        non_target = (
            db.query(CvScoreJob).filter(CvScoreJob.application_id == app_ids[2]).one()
        )
        db.refresh(owned)
        assert owned.status == SCORE_JOB_ERROR
        assert non_target.status == SCORE_JOB_PENDING
    finally:
        db.close()


def test_due_fanout_recovery_claim_is_bounded_by_durable_retry_receipt(
    session_factory,
    monkeypatch,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    monkeypatch.setattr(
        "app.services.scoring_batch_fanout_recovery.SessionLocal",
        session_factory,
    )
    claimed_at = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    db = session_factory()
    try:
        scanned, payloads = claim_due_scoring_fanouts(
            db,
            limit=10,
            now=claimed_at,
        )
        db.commit()
        assert scanned == 1
        assert payloads == [
            {
                "role_id": role_id,
                "organization_id": db.get(Role, role_id).organization_id,
                "include_scored": False,
                "applied_after": None,
                "run_id": run_id,
            }
        ]

        scanned_again, payloads_again = claim_due_scoring_fanouts(
            db,
            limit=10,
            now=claimed_at,
        )
        assert scanned_again == 0
        assert payloads_again == []
    finally:
        db.close()

    assert mark_scoring_fanout_published(
        run_id,
        role_id=role_id,
        organization_id=payloads[0]["organization_id"],
        now=claimed_at,
    )
    db = session_factory()
    try:
        counters = dict(db.get(BackgroundJobRun, run_id).counters)
        assert counters["fanout_dispatch_attempts"] == 1
        assert counters["fanout_last_published_at"] == claimed_at.isoformat()
    finally:
        db.close()


def test_recovery_task_republishes_lost_root_without_provider_work(
    session_factory,
    monkeypatch,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    monkeypatch.setattr(
        "app.services.scoring_batch_fanout_recovery.SessionLocal",
        session_factory,
    )
    monkeypatch.setattr(
        "app.services.scoring_batch_successor_reconcile.reconcile_queued_scoring_successors",
        lambda limit: {"examined": 0, "started": 0},
    )
    monkeypatch.setattr(
        "app.services.scoring_backfill_recovery.reconcile_scoring_backfill_fanout",
        lambda limit: {"examined": 0, "advanced": 0},
    )
    monkeypatch.setattr(
        "app.services.scoring_backfill_terminal_reconcile.reconcile_scoring_backfill_parents",
        lambda limit: {"examined": 0, "completed": 0},
    )
    monkeypatch.setattr(
        "app.services.scoring_batch_terminal_reconcile.reconcile_drained_scoring_batches",
        lambda limit: {"examined": 0, "completed": 0},
    )
    dispatched = []
    monkeypatch.setattr(
        batch_score_role,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    result = recover_scoring_batch_dispatches.run(limit=10)

    assert result["scanned"] == 1
    assert result["claimed"] == 1
    assert result["kicked"] == 1
    assert result["publish_failed"] == 0
    assert result["backfills"] == {"examined": 0, "advanced": 0}
    assert result["backfill_terminals"] == {"examined": 0, "completed": 0}
    assert result["terminals"] == {"examined": 0, "completed": 0}
    assert dispatched == [
        (
            (role_id,),
            {
                "include_scored": False,
                "applied_after": None,
                "run_id": run_id,
            },
        )
    ]


@pytest.mark.parametrize(
    "corrupt_targets",
    (
        None,
        [],
        ["bad", 0, -1, True],
        "partially_invalid",
        "duplicate",
        "unsorted",
    ),
)
def test_durable_run_fails_closed_when_exact_target_snapshot_is_corrupt(
    session_factory,
    monkeypatch,
    corrupt_targets,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids)
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        counters = dict(run.counters)
        if corrupt_targets is None:
            counters.pop("target_application_ids")
        elif corrupt_targets == "partially_invalid":
            counters["target_application_ids"] = [app_ids[0], "bad"]
        elif corrupt_targets == "duplicate":
            counters["target_application_ids"] = [app_ids[0], app_ids[0]]
        elif corrupt_targets == "unsorted":
            counters["target_application_ids"] = list(reversed(app_ids))
        else:
            counters["target_application_ids"] = corrupt_targets
        run.counters = counters
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        lambda *_args, **_kwargs: pytest.fail(
            "corrupt target run reached provider work"
        ),
    )
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.enqueue_score",
        lambda *_args, **_kwargs: pytest.fail("corrupt target run reached scoring"),
    )

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "invalid_run"
    assert result["error"] == "scoring_batch_invalid_target_snapshot"
    db = session_factory()
    try:
        run = db.get(BackgroundJobRun, run_id)
        assert run.status == "failed"
        assert run.error == "scoring_batch_invalid_target_snapshot"
        assert run.finished_at is not None
        assert run.counters["fanout_complete"] is True
        assert run.counters["target_application_ids"] == []
        assert db.query(CvScoreJob).count() == 0
    finally:
        db.close()


def test_slow_fetch_renews_delivery_lease_before_every_provider_call(
    session_factory,
    monkeypatch,
):
    role_id, app_ids = _seed_role_with_apps(session_factory)
    run_id = _seed_run(session_factory, role_id, app_ids[1:])
    db = session_factory()
    try:
        second = db.get(CandidateApplication, app_ids[1])
        second.candidate.cv_text = None
        second.candidate.workable_candidate_id = "wk-b"
        second.source = "workable"
        second.workable_candidate_id = "wk-b"
        db.commit()
    finally:
        db.close()

    observed_leases = []

    def _fetch(app, _candidate, db, _org):
        run = db.get(BackgroundJobRun, run_id)
        observed_leases.append(
            datetime.fromisoformat(run.counters["fanout_lease_expires_at"])
        )
        app.cv_text = f"Fetched CV for {app.id}"
        if len(observed_leases) == 1:
            counters = dict(run.counters)
            counters["fanout_lease_expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            run.counters = counters
            db.commit()
        return True

    monkeypatch.setattr(
        "app.domains.assessments_runtime.applications_routes._try_fetch_cv_from_workable",
        _fetch,
    )
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.enqueue_score",
        lambda *_args, **_kwargs: None,
    )

    result = batch_score_role(role_id, include_scored=True, run_id=run_id)

    assert result["status"] == "failed"
    assert len(observed_leases) == 2
    assert all(lease > datetime.now(timezone.utc) for lease in observed_leases)


def test_failure_receipt_recovery_chunks_every_target_query(session_factory):
    role_id, _ = _seed_role_with_apps(session_factory)
    target_ids = list(range(1, 1_202))
    run_id = _seed_run(session_factory, role_id, target_ids)
    db = session_factory()
    target_query_parameter_counts = []

    def _record_target_query(_conn, _cursor, statement, parameters, *_args):
        if "FROM cv_score_jobs" in statement and "application_id IN" in statement:
            target_query_parameter_counts.append(len(parameters))

    event.listen(db.get_bind(), "before_cursor_execute", _record_target_query)
    try:
        run = db.get(BackgroundJobRun, run_id)
        progress = ScoringBatchProgress(
            role_id,
            run_id,
            False,
            None,
            total=len(target_ids),
            target_application_ids=target_ids,
        )

        progress.fail(db, run)

        assert sorted(count - 1 for count in target_query_parameter_counts) == [
            201,
            500,
            500,
        ]
        assert run.status == "failed"
        assert run.finished_at is not None
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", _record_target_query)
        db.close()
