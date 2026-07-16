"""Focused regressions for pre-screen throughput, batching, and impact audit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import inspect as sa_inspect

from app.domains.compliance.prescreen_impact_service import (
    _gate_passed,
    _impact_rows_query,
    compute_aggregate_metrics,
    run_prescreen_adverse_impact_audit,
)
from app.models.background_job_run import (
    JOB_KIND_PRE_SCREEN_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.eeo_response import EEOResponse
from app.models.organization import Organization
from app.models.prescreen_adverse_impact_audit import PrescreenAdverseImpactAudit
from app.models.prescreen_batch_item import PrescreenBatchItem
from app.models.role import Role
from app.models.user import User
from app.platform.config import settings
from app.platform.database import SessionLocal
from app.tasks.prescreen_tasks import (
    batch_pre_screen_role_job,
    pre_screen_application_job,
    recover_prescreen_batch_dispatches,
    select_prescreen_target_ids,
)
from app.tasks.agent_tasks import agent_scoring_backlog_sweep
from app.tasks.celery_app import celery_app


def _role(db, suffix: str = "ops") -> tuple[Organization, Role]:
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Engineer",
        job_spec_text="Build reliable Python systems",
    )
    db.add(role)
    db.flush()
    return org, role


def _app(db, org, role, idx: int, *, score: float | None = None):
    candidate = Candidate(
        organization_id=org.id,
        email=f"prescreen-{idx}-{id(db)}@example.test",
        full_name=f"Candidate {idx}",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        cv_text="Python engineer resume",
        genuine_pre_screen_score_100=score,
    )
    db.add(app)
    db.flush()
    return app


def test_target_selector_matches_error_backoff_and_stale_cv(db):
    org, role = _role(db, "selector")
    never = _app(db, org, role, 1)
    current = _app(db, org, role, 2)
    current.pre_screen_run_at = datetime.now(timezone.utc)
    stale = _app(db, org, role, 3)
    stale.pre_screen_run_at = datetime.now(timezone.utc) - timedelta(days=1)
    stale.cv_uploaded_at = datetime.now(timezone.utc)
    recent_error = _app(db, org, role, 4)
    recent_error.pre_screen_run_at = datetime.now(timezone.utc)
    recent_error.pre_screen_error_reason = "retryable"
    old_error = _app(db, org, role, 5)
    old_error.pre_screen_run_at = datetime.now(timezone.utc) - timedelta(days=1)
    old_error.pre_screen_error_reason = "retryable"
    db.commit()

    selected = select_prescreen_target_ids(
        db,
        role_id=role.id,
        organization_id=org.id,
        refresh=False,
    )
    assert selected == [never.id, stale.id, old_error.id]


def test_fast_backlog_sweep_decouples_scoring_drain_from_hourly_agent_cycle(
    db, monkeypatch
):
    org, role = _role(db, "backlog")
    role.agentic_mode_enabled = True
    _app(db, org, role, 1)
    db.commit()
    seen: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "app.tasks.agent_tasks._auto_enqueue_scoring",
        lambda db, *, role, limit, strict: seen.append((int(role.id), limit)) or 1,
    )
    result = agent_scoring_backlog_sweep.run(per_role_limit=25, role_limit=10)
    assert result == {
        "status": "ok",
        "roles": 1,
        "enqueued": 1,
        "errors": 0,
        "per_role_limit": 25,
    }
    assert seen == [(role.id, 25)]


def test_prescreen_dispatch_recovery_is_registered_and_scheduled():
    task_name = "app.tasks.prescreen_tasks.recover_prescreen_batch_dispatches"
    assert task_name in celery_app.tasks
    schedule = celery_app.conf.beat_schedule[
        "recover-prescreen-batch-dispatches-every-minute"
    ]
    assert schedule["task"] == task_name
    assert schedule["schedule"] == 60.0


def test_batch_materializes_durable_items_before_fanout(db, monkeypatch):
    assert batch_pre_screen_role_job.acks_late is True
    assert batch_pre_screen_role_job.reject_on_worker_lost is True
    assert pre_screen_application_job.acks_late is True
    assert pre_screen_application_job.reject_on_worker_lost is True
    org, role = _role(db, "materialize")
    apps = [_app(db, org, role, idx) for idx in range(3)]
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=org.id,
        status="queued",
        counters={"total": 3},
    )
    db.add(run)
    db.commit()

    dispatched: list[int] = []
    monkeypatch.setattr(
        pre_screen_application_job,
        "apply_async",
        lambda args, kwargs: dispatched.append(int(args[0])),
    )
    result = batch_pre_screen_role_job.run(
        role.id,
        org.id,
        run_id=run.id,
        refresh=False,
    )
    assert result["status"] == "enqueued"
    assert len(dispatched) == 3
    items = db.query(PrescreenBatchItem).filter_by(run_id=run.id).all()
    assert {item.application_id for item in items} == {app.id for app in apps}
    assert all(item.status == "queued" for item in items)


def test_manual_batch_route_persists_then_dispatches_celery(db, monkeypatch):
    from app.domains.assessments_runtime.applications_routes import (
        batch_pre_screen_role,
    )

    org, role = _role(db, "route")
    _app(db, org, role, 1)
    user = User(
        email=f"owner-{id(db)}@example.test",
        hashed_password="x",
        full_name="Owner",
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    dispatched: list[tuple] = []
    monkeypatch.setattr(
        batch_pre_screen_role_job,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    result = batch_pre_screen_role(
        role.id,
        refresh=False,
        dry_run=False,
        db=db,
        current_user=user,
    )
    assert result["status"] == "started"
    assert result["total"] == 1
    assert dispatched == [
        (
            (role.id, org.id),
            {"run_id": result["run_id"], "refresh": False},
        )
    ]
    run = db.get(BackgroundJobRun, result["run_id"])
    assert run.kind == JOB_KIND_PRE_SCREEN_BATCH
    assert run.status == "queued"


def test_dispatch_recovery_releases_broker_failure_and_leases_success(
    db, monkeypatch
):
    org, role = _role(db, "broker-recovery")
    app = _app(db, org, role, 1)
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=org.id,
        status="running",
        counters={"total": 1, "refresh": True},
    )
    db.add(run)
    db.flush()
    item = PrescreenBatchItem(
        run_id=run.id,
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        status="queued",
    )
    db.add(item)
    db.commit()

    def broker_down(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(pre_screen_application_job, "apply_async", broker_down)
    failed = recover_prescreen_batch_dispatches.run(limit=10)
    assert failed["claimed"] == 1
    assert failed["dispatch_errors"] == 1
    db.expire_all()
    after_failure = db.get(PrescreenBatchItem, item.id)
    assert after_failure.dispatch_attempts == 1
    assert after_failure.dispatch_token
    assert after_failure.dispatch_lease_until
    assert after_failure.error_code == "broker_dispatch_retry"

    # The short ambiguity lease prevents an immediate duplicate publication.
    assert recover_prescreen_batch_dispatches.run(limit=10)["claimed"] == 0
    after_failure.dispatch_lease_until = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db.commit()

    published: list[tuple] = []
    monkeypatch.setattr(
        pre_screen_application_job,
        "apply_async",
        lambda *args, **kwargs: published.append((args, kwargs)),
    )
    recovered = recover_prescreen_batch_dispatches.run(limit=10)
    assert recovered["enqueued"] == 1
    assert published[0][1]["kwargs"] == {"refresh": True}
    db.expire_all()
    after_publish = db.get(PrescreenBatchItem, item.id)
    assert after_publish.dispatch_attempts == 2
    assert after_publish.dispatch_token
    assert after_publish.dispatch_lease_until
    assert after_publish.last_dispatched_at

    # A second Beat instance sees the live lease and cannot publish a duplicate.
    assert recover_prescreen_batch_dispatches.run(limit=10)["claimed"] == 0
    assert len(published) == 1


def test_expired_worker_lease_is_recovered_then_duplicate_delivery_is_free(
    db, monkeypatch
):
    org, role = _role(db, "worker-recovery")
    app = _app(db, org, role, 1)
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=org.id,
        status="running",
        counters={"total": 1, "refresh": False},
    )
    db.add(run)
    db.flush()
    item = PrescreenBatchItem(
        run_id=run.id,
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        status="queued",
        dispatch_token="lost-worker-token",
        dispatch_lease_until=datetime.now(timezone.utc) - timedelta(minutes=1),
        dispatch_attempts=1,
    )
    db.add(item)
    db.commit()

    publications: list[int] = []
    monkeypatch.setattr(
        pre_screen_application_job,
        "apply_async",
        lambda args, kwargs: publications.append(int(args[0])),
    )
    recovered = recover_prescreen_batch_dispatches.run(limit=10)
    assert recovered["enqueued"] == 1
    assert publications == [item.id]

    calls = {"screen": 0}

    def fake_screen(live_app, *, db, client):
        calls["screen"] += 1
        live_app.pre_screen_run_at = datetime.now(timezone.utc)
        live_app.genuine_pre_screen_score_100 = 72
        live_app.pre_screen_evidence = {"fraud_capped": False}
        return {"status": "ok"}

    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_client_for_org",
        lambda _org: MagicMock(),
    )
    monkeypatch.setattr(
        "app.services.pre_screening_service.execute_pre_screen_only", fake_screen
    )
    monkeypatch.setattr(
        "app.tasks.automation_tasks.run_application_auto_reject.delay",
        lambda _app_id: None,
    )
    assert pre_screen_application_job.run(item.id)["status"] == "done"
    assert pre_screen_application_job.run(item.id)["status"] == "already_terminal"
    assert calls["screen"] == 1
    db.expire_all()
    terminal = db.get(PrescreenBatchItem, item.id)
    assert terminal.dispatch_token is None
    assert terminal.dispatch_lease_until is None


def test_paid_response_then_worker_death_surfaces_ambiguity_without_repay(
    db, monkeypatch
):
    class SimulatedWorkerDeath(BaseException):
        pass

    org, role = _role(db, "paid-death")
    app = _app(db, org, role, 1)
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=org.id,
        status="running",
        counters={"total": 1, "refresh": False},
    )
    db.add(run)
    db.flush()
    item = PrescreenBatchItem(
        run_id=run.id,
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        status="queued",
    )
    db.add(item)
    db.commit()

    calls = {"provider": 0}

    def response_then_die(live_app, *, db, client):
        calls["provider"] += 1
        # The attempt marker is already committed and readable through a
        # separate connection; no item lock/connection spans the paid call.
        probe = SessionLocal()
        try:
            assert probe.get(PrescreenBatchItem, item.id).status == "attempting"
        finally:
            probe.close()
        live_app.genuine_pre_screen_score_100 = 88
        raise SimulatedWorkerDeath()

    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_client_for_org",
        lambda _org: MagicMock(),
    )
    monkeypatch.setattr(
        "app.services.pre_screening_service.execute_pre_screen_only",
        response_then_die,
    )
    with pytest.raises(SimulatedWorkerDeath):
        pre_screen_application_job.run(item.id)

    db.expire_all()
    assert db.get(CandidateApplication, app.id).genuine_pre_screen_score_100 is None
    stranded = db.get(PrescreenBatchItem, item.id)
    assert stranded.status == "attempting"
    stranded.provider_attempt_started_at = datetime.now(timezone.utc) - timedelta(
        minutes=7
    )
    db.commit()

    publications: list[int] = []
    monkeypatch.setattr(
        pre_screen_application_job,
        "apply_async",
        lambda args, kwargs: publications.append(int(args[0])),
    )
    recovered = recover_prescreen_batch_dispatches.run(limit=10)
    assert recovered["ambiguous"] == 1
    assert publications == []
    db.expire_all()
    surfaced = db.get(PrescreenBatchItem, item.id)
    assert surfaced.status == "ambiguous"
    assert surfaced.error_code == "provider_attempt_outcome_unknown"
    parent = db.get(BackgroundJobRun, run.id)
    assert parent.status == "completed_with_errors"
    assert parent.counters["ambiguous"] == 1
    assert pre_screen_application_job.run(item.id)["status"] == "already_terminal"
    assert calls["provider"] == 1


def test_prescreen_item_runs_once_and_updates_durable_progress(db, monkeypatch):
    org, role = _role(db, "item")
    app = _app(db, org, role, 1)
    run = BackgroundJobRun(
        kind=JOB_KIND_PRE_SCREEN_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=org.id,
        status="running",
        counters={"total": 1},
    )
    db.add(run)
    db.flush()
    item = PrescreenBatchItem(
        run_id=run.id,
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        status="queued",
    )
    db.add(item)
    db.commit()

    calls = {"screen": 0, "reject": 0}

    def fake_screen(live_app, *, db, client):
        calls["screen"] += 1
        live_app.pre_screen_run_at = datetime.now(timezone.utc)
        live_app.genuine_pre_screen_score_100 = 72
        live_app.pre_screen_evidence = {"fraud_capped": False}
        return {"status": "ok"}

    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_client_for_org",
        lambda _org: MagicMock(),
    )
    monkeypatch.setattr(
        "app.services.pre_screening_service.execute_pre_screen_only", fake_screen
    )
    monkeypatch.setattr(
        "app.tasks.automation_tasks.run_application_auto_reject.delay",
        lambda _app_id: calls.__setitem__("reject", calls["reject"] + 1),
    )

    first = pre_screen_application_job.run(item.id, refresh=False)
    second = pre_screen_application_job.run(item.id, refresh=False)
    assert first["status"] == "done"
    assert second["status"] == "already_terminal"
    assert calls == {"screen": 1, "reject": 1}
    db.expire_all()
    assert db.get(PrescreenBatchItem, item.id).status == "done"
    refreshed_run = db.get(BackgroundJobRun, run.id)
    assert refreshed_run.status == "completed"
    assert refreshed_run.counters["processed"] == 1


def test_impact_math_suppresses_small_labels_and_flags_four_fifths():
    records = []
    for _ in range(10):
        records.append(
            ({"gender": "reference"}, {"pre_screen_gate_pass": True})
        )
    for selected in (True, True, False, False, False, False, False, False, False, False):
        records.append(
            ({"gender": "comparison"}, {"pre_screen_gate_pass": selected})
        )
    records.append(({"gender": "tiny-private-label"}, {"pre_screen_gate_pass": False}))

    metrics, violations, comparisons = compute_aggregate_metrics(
        records,
        impact_ratio_min=0.8,
        min_cell_n=5,
    )
    gender = metrics["pre_screen_gate_pass"]["gender"]
    assert "tiny-private-label" not in str(gender)
    assert gender["suppressed_n"] == 1
    assert comparisons == 1
    assert violations[0]["segment"] == "comparison"
    assert violations[0]["impact_ratio"] == 0.2


def test_impact_audit_honors_legacy_yes_and_no_verdicts():
    passed = SimpleNamespace(
        pre_screen_evidence={},
        cv_match_details={"pre_screen_decision": "yes"},
        genuine_pre_screen_score_100=None,
        cv_match_score=None,
    )
    filtered = SimpleNamespace(
        pre_screen_evidence={},
        cv_match_details={"pre_screen_decision": "no"},
        genuine_pre_screen_score_100=None,
        cv_match_score=None,
    )
    assert _gate_passed(passed) is True
    assert _gate_passed(filtered) is False


def test_process_status_prefers_celery_redis_progress(db, monkeypatch):
    from app.domains.assessments_runtime import applications_routes as routes

    org, role = _role(db, "process-status")
    user = User(
        email=f"process-owner-{id(db)}@example.test",
        hashed_password="x",
        full_name="Owner",
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    routes._process_progress[role.id] = {"status": "running"}
    monkeypatch.setattr(
        routes,
        "_read_process_progress",
        lambda _role_id: {"status": "completed", "current_step": None},
    )

    result = routes.process_role_status(role.id, db=db, current_user=user)

    assert result["status"] == "completed"
    assert result["role_name"] == role.name


def test_rolling_audit_persists_aggregates_and_capability_reads_latest(db):
    org, role = _role(db, "impact")
    now = datetime.now(timezone.utc)
    for idx in range(20):
        passed = idx < 10 or idx in (10, 11)
        app = _app(db, org, role, idx, score=80 if passed else 10)
        app.pre_screen_run_at = now - timedelta(hours=1)
        app.pre_screen_evidence = {
            "gate_threshold_enforced": 30,
            "fraud_capped": False,
        }
        db.add(
            EEOResponse(
                organization_id=org.id,
                application_id=app.id,
                gender="reference" if idx < 10 else "comparison",
                declined_to_answer=False,
            )
        )
    db.commit()

    audit = run_prescreen_adverse_impact_audit(
        db,
        organization_id=org.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(seconds=1),
        impact_ratio_min=0.8,
        min_cell_n=5,
    )
    db.commit()
    assert audit.status == "violations"
    assert audit.sample_size == 20
    assert audit.violations_json
    assert db.query(PrescreenAdverseImpactAudit).count() == 1

def test_impact_query_does_not_hydrate_large_candidate_payloads(db):
    org, role = _role(db, "impact-projection")
    app = _app(db, org, role, 1, score=75)
    app.cv_text = "large cv payload " * 50_000
    app.pre_screen_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    app.pre_screen_evidence = {
        "gate_threshold_enforced": 30,
        "fraud_capped": False,
    }
    db.add(
        EEOResponse(
            organization_id=org.id,
            application_id=app.id,
            gender="segment",
            declined_to_answer=False,
        )
    )
    org_id = int(org.id)
    db.commit()
    db.expunge_all()
    now = datetime.now(timezone.utc)

    query = _impact_rows_query(
        db,
        organization_id=org_id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(seconds=1),
    )
    sql = str(query.statement).lower()
    assert "candidate_applications.cv_text" not in sql
    rows = query.all()
    assert len(rows) == 1
    unloaded = sa_inspect(rows[0][1]).unloaded
    assert "cv_text" in unloaded
    assert "pre_screen_evidence" not in unloaded


def test_scheduled_monitor_is_inert_until_opted_in(monkeypatch):
    from app.tasks.compliance_tasks import audit_prescreen_adverse_impact

    monkeypatch.setattr(
        settings, "PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED", False
    )
    assert audit_prescreen_adverse_impact.run() == {
        "status": "disabled",
        "audited": 0,
        "violations": 0,
    }
