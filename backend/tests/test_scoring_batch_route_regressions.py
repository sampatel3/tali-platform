from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.assessments_runtime import (
    applications_routes,
    scoring_batch_runtime,
    scoring_batch_state,
)
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
from app.tasks import scoring_tasks
from app.services import (
    scoring_batch_successor_dispatch,
    scoring_batch_successor_reconcile,
    scoring_batch_successors,
)


def _seed_batch_scope(db, *, label: str = "route-regression"):
    organization = Organization(
        name=f"Batch {label}",
        slug=f"batch-{label}-{id(db)}",
    )
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=organization.id,
        name="Platform Engineer",
        source="manual",
        job_spec_text="Build and operate a reliable platform.",
    )
    db.add(role)
    db.flush()
    return organization, role


def _add_application(
    db,
    *,
    organization: Organization,
    role: Role,
    label: str,
    applied_at: datetime,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization.id,
        email=f"{label}-{id(db)}@example.test",
        full_name=label,
        workable_created_at=applied_at,
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="manual",
        cv_text=f"CV for {label}",
    )
    db.add(application)
    db.flush()
    return application


def _allow_role(monkeypatch, role: Role) -> None:
    monkeypatch.setattr(
        applications_routes,
        "require_job_permission",
        lambda *_args, **_kwargs: role,
    )


def _add_score_job(
    db,
    *,
    application: CandidateApplication,
    status: str,
    batch_run_id: int | None = None,
    cache_hit: str | None = None,
) -> CvScoreJob:
    job = CvScoreJob(
        application_id=application.id,
        role_id=application.role_id,
        batch_run_id=batch_run_id,
        status=status,
        cache_hit=cache_hit,
        requires_active_agent=False,
    )
    if status in {SCORE_JOB_DONE, SCORE_JOB_ERROR}:
        job.finished_at = datetime.now(timezone.utc)
    db.add(job)
    db.flush()
    return job


def test_batch_score_dry_run_respects_applied_after(db, monkeypatch) -> None:
    organization, role = _seed_batch_scope(db, label="dry-run-cutoff")
    _add_application(
        db,
        organization=organization,
        role=role,
        label="Before",
        applied_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
    )
    _add_application(
        db,
        organization=organization,
        role=role,
        label="After",
        applied_at=datetime(2026, 7, 1, 0, 1, tzinfo=timezone.utc),
    )
    db.commit()
    _allow_role(monkeypatch, role)

    response = applications_routes.batch_score_role(
        role.id,
        include_scored=False,
        applied_after="2026-07-01T00:00:00Z",
        dry_run=True,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["total"] == 1
    assert response["will_score"] == 1


def test_batch_score_initial_run_respects_applied_after(db, monkeypatch) -> None:
    organization, role = _seed_batch_scope(db, label="initial-cutoff")
    _add_application(
        db,
        organization=organization,
        role=role,
        label="Before",
        applied_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
    )
    _add_application(
        db,
        organization=organization,
        role=role,
        label="After",
        applied_at=datetime(2026, 7, 1, 0, 1, tzinfo=timezone.utc),
    )
    db.commit()
    dispatched: list[tuple[tuple, dict]] = []
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(applications_routes, "_create_job_run", lambda **_kwargs: 901)
    monkeypatch.setattr(
        applications_routes, "_write_batch_meta", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(applications_routes, "_clear_cancel_flag", lambda *_args: None)
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    response = applications_routes.batch_score_role(
        role.id,
        include_scored=False,
        applied_after="2026-07-01T00:00:00Z",
        dry_run=False,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "started"
    assert response["total"] == 1
    assert response["run_id"] == 901
    assert dispatched == [
        (
            (role.id,),
            {
                "include_scored": False,
                "applied_after": "2026-07-01T00:00:00Z",
                "run_id": 901,
            },
        )
    ]


def test_initial_broker_failure_keeps_durable_root_for_recovery(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="broker-recovery")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Recoverable",
        applied_at=datetime.now(timezone.utc),
    )
    db.commit()

    def _create(**kwargs):
        run = BackgroundJobRun(**kwargs)
        db.add(run)
        db.commit()
        return int(run.id)

    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(applications_routes, "_create_job_run", _create)
    monkeypatch.setattr(
        applications_routes, "_write_batch_meta", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(applications_routes, "_clear_cancel_flag", lambda *_args: None)

    def _broker_failure(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(scoring_tasks.batch_score_role, "delay", _broker_failure)

    response = applications_routes.batch_score_role(
        role.id,
        include_scored=False,
        applied_after=None,
        dry_run=False,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    db.expire_all()
    run = (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.scope_id == role.id)
        .order_by(BackgroundJobRun.id.desc())
        .one()
    )
    counters = dict(run.counters)
    assert response["status"] == "started"
    assert response["dispatch_pending"] is True
    assert run.status == "dispatching"
    assert run.finished_at is None
    assert counters["target_application_ids"] == [application.id]
    assert counters["fanout_complete"] is False
    assert counters["fanout_last_publish_error"] == "broker_publish_failed"
    assert counters["fanout_dispatch_attempts"] == 1


def test_initial_start_locks_before_rechecking_durable_run(
    monkeypatch,
) -> None:
    organization = SimpleNamespace(id=31)
    role = SimpleNamespace(name="Platform Engineer", job_spec_text="Build systems")
    active_run = SimpleNamespace(
        id=905,
        organization_id=organization.id,
        status="running",
        counters={"selected_total": 4, "scored": 0},
        started_at=datetime.now(timezone.utc),
        finished_at=None,
    )
    events: list[str] = []
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "role_has_job_spec", lambda _role: True)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        applications_routes,
        "_lock_scoring_start_scope",
        lambda _db, _role_id: events.append("lock"),
    )

    def _latest(*_args, **_kwargs):
        events.append("latest")
        return active_run

    monkeypatch.setattr(applications_routes, "_latest_scoring_run", _latest)
    monkeypatch.setattr(
        applications_routes, "_write_batch_queue", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        scoring_batch_runtime,
        "queue_scoring_successor",
        lambda *_args, **_kwargs: True,
    )

    response = applications_routes.batch_score_role(
        17,
        include_scored=False,
        applied_after=None,
        dry_run=False,
        db=object(),
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert events == ["lock", "latest"]
    assert response["status"] == "queued"
    assert response["run_id"] == 905


def test_postgres_initial_start_uses_transaction_scoped_advisory_lock() -> None:
    calls: list[tuple[str, dict]] = []

    class _Db:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement, parameters):
            calls.append((str(statement), parameters))

    applications_routes._lock_scoring_start_scope(_Db(), 77)

    assert calls == [
        (
            "SELECT pg_advisory_xact_lock(:namespace, :role_id)",
            {
                "namespace": applications_routes._SCORING_START_ADVISORY_NAMESPACE,
                "role_id": 77,
            },
        )
    ]


def test_queued_successor_claim_is_atomic_and_preserves_applied_after(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="queued-cutoff")
    _add_application(
        db,
        organization=organization,
        role=role,
        label="Before",
        applied_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
    )
    _add_application(
        db,
        organization=organization,
        role=role,
        label="After",
        applied_at=datetime(2026, 7, 1, 0, 1, tzinfo=timezone.utc),
    )
    db.commit()
    queue_key = f"{applications_routes._BATCH_QUEUE_PREFIX}{role.id}"

    class _AtomicRedis:
        def __init__(self):
            self.values = {
                queue_key: json.dumps(
                    {
                        "include_scored": False,
                        "applied_after": "2026-07-01T00:00:00Z",
                        "queue_id": "queued-request-1",
                    }
                )
            }
            self.eval_calls = 0

        def eval(self, script, key_count, key):
            assert script == applications_routes._BATCH_QUEUE_CLAIM_LUA
            assert key_count == 1
            self.eval_calls += 1
            return self.values.pop(key, None)

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, **_kwargs):
            self.values[key] = value

        def delete(self, key):
            self.values.pop(key, None)

    redis = _AtomicRedis()
    store = {
        role.id: {
            "status": "completed",
            "organization_id": organization.id,
            "run_id": 900,
            "total": 0,
            "scored": 0,
            "errors": 0,
            "pre_screened_out": 0,
            "started_at": datetime(2026, 7, 18, 7, tzinfo=timezone.utc),
            "terminal_at": datetime(2026, 7, 18, 8, tzinfo=timezone.utc),
        }
    }
    dispatched: list[tuple[tuple, dict]] = []
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(applications_routes, "_redis_client", lambda: redis)
    monkeypatch.setattr(
        applications_routes, "_latest_scoring_run", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(applications_routes, "_create_job_run", lambda **_kwargs: 902)
    monkeypatch.setattr(
        scoring_batch_successor_dispatch,
        "reserve_scoring_fanout_publish",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        applications_routes, "_update_job_run", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_terminal_counts",
        lambda *_args, **_kwargs: (0, 0, 0),
    )
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    first = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    second = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert first["status"] == "running"
    assert first["total"] == 1
    assert first["run_id"] == 902
    assert second["status"] == "running"
    assert second["run_id"] == 902
    assert redis.eval_calls == 1
    assert dispatched == [
        (
            (role.id,),
            {
                "include_scored": False,
                "applied_after": "2026-07-01T00:00:00Z",
                "run_id": 902,
            },
        )
    ]


def test_successor_is_not_claimed_when_durable_terminal_update_fails(
    monkeypatch,
) -> None:
    started_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    store = {
        42: {
            "status": "running",
            "organization_id": 11,
            "run_id": 906,
            "total": 1,
            "scored": 0,
            "errors": 0,
            "pre_screened_out": 0,
            "started_at": started_at,
        }
    }
    claims: list[int] = []
    deleted_meta: list[int] = []
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(
        applications_routes,
        "require_job_permission",
        lambda *_args, **_kwargs: SimpleNamespace(name="Platform Engineer"),
    )
    monkeypatch.setattr(
        applications_routes, "_latest_scoring_run", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_terminal_counts",
        lambda *_args, **_kwargs: (1, 0, 0),
    )
    monkeypatch.setattr(
        applications_routes, "_update_job_run", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        applications_routes,
        "_claim_batch_queue",
        lambda role_id: claims.append(role_id),
    )
    monkeypatch.setattr(
        applications_routes,
        "_read_batch_queue",
        lambda _role_id: {"include_scored": True},
    )
    monkeypatch.setattr(
        applications_routes,
        "_delete_batch_meta",
        lambda role_id: deleted_meta.append(role_id),
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_role_name",
        lambda *_args, **_kwargs: "Platform Engineer",
    )

    response = applications_routes.batch_score_status(
        42,
        db=object(),
        current_user=SimpleNamespace(organization_id=11),
    )

    assert response["status"] == "running"
    assert response["queued"] == {"include_scored": True}
    assert store[42]["status"] == "running"
    assert claims == []
    assert deleted_meta == []


def test_durable_run_supersedes_stale_process_local_identity(db, monkeypatch) -> None:
    organization, role = _seed_batch_scope(db, label="durable-newer")
    started_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    durable = BackgroundJobRun(
        id=920,
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={
            "selected_total": 2,
            "scored": 0,
            "errors": 0,
            "pre_screened_out": 0,
            "include_scored": False,
        },
        started_at=started_at,
    )
    db.add(durable)
    db.commit()
    store = {
        role.id: {
            "status": "running",
            "organization_id": organization.id,
            "run_id": 919,
            "total": 7,
            "scored": 4,
            "errors": 0,
            "pre_screened_out": 0,
            "started_at": started_at - timedelta(hours=1),
        }
    }
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(applications_routes, "_read_batch_queue", lambda _role_id: None)
    monkeypatch.setattr(
        applications_routes,
        "batch_score_terminal_counts",
        lambda *_args, **_kwargs: (0, 0, 0),
    )

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "running"
    assert response["run_id"] == 920
    assert response["total"] == 2
    assert store[role.id]["run_id"] == 920


def test_active_discovery_recovers_durable_run_after_local_memory_loss(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="durable-discovery")
    foreign_organization, foreign_role = _seed_batch_scope(
        db, label="foreign-discovery"
    )
    started_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    db.add_all(
        [
            BackgroundJobRun(
                id=930,
                kind=JOB_KIND_SCORING_BATCH,
                scope_kind=SCOPE_KIND_ROLE,
                scope_id=role.id,
                organization_id=organization.id,
                status="running",
                counters={
                    "selected_total": 3,
                    "scored": 1,
                    "errors": 0,
                    "pre_screened_out": 0,
                },
                started_at=started_at,
            ),
            BackgroundJobRun(
                id=931,
                kind=JOB_KIND_SCORING_BATCH,
                scope_kind=SCOPE_KIND_ROLE,
                scope_id=foreign_role.id,
                organization_id=foreign_organization.id,
                status="running",
                counters={"selected_total": 9},
                started_at=started_at,
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})

    response = applications_routes.get_active_batch_scores(
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert len(response["active"]) == 1
    recovered = response["active"][0]
    assert recovered["role_id"] == role.id
    assert recovered["role_name"] == "Platform Engineer"
    assert recovered["run_id"] == 930
    assert recovered["status"] == "running"
    assert recovered["total"] == 3
    assert recovered["scored"] == 1
    assert recovered["errors"] == 0
    assert recovered["pre_screened_out"] == 0
    assert recovered["started_at"] is not None
    assert recovered["terminal_at"] is None


def test_recent_durable_discovery_keeps_only_latest_run_per_role(db) -> None:
    organization, first_role = _seed_batch_scope(db, label="latest-per-role")
    second_role = Role(
        organization_id=organization.id,
        name="Security Engineer",
        source="manual",
    )
    db.add(second_role)
    db.flush()
    foreign_organization, foreign_role = _seed_batch_scope(
        db,
        label="latest-per-role-foreign",
    )
    now = datetime.now(timezone.utc)

    def _run(run_id: int, role: Role, organization_id: int) -> BackgroundJobRun:
        return BackgroundJobRun(
            id=run_id,
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role.id,
            organization_id=organization_id,
            status="completed",
            counters={"selected_total": 1},
            started_at=now - timedelta(minutes=5),
            finished_at=now,
        )

    db.add_all(
        [
            _run(950, second_role, organization.id),
            _run(951, first_role, organization.id),
            _run(952, first_role, organization.id),
            _run(999, foreign_role, foreign_organization.id),
        ]
    )
    db.commit()

    runs = applications_routes._recent_scoring_runs(
        db,
        organization_id=organization.id,
        now=now,
        limit=2,
    )

    assert [(run.id, run.scope_id) for run in runs] == [
        (952, first_role.id),
        (950, second_role.id),
    ]


def test_abandoned_durable_run_eventually_becomes_terminal(db, monkeypatch) -> None:
    organization, role = _seed_batch_scope(db, label="abandoned-run")
    durable = BackgroundJobRun(
        id=940,
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={
            "selected_total": 5,
            "scored": 0,
            "errors": 0,
            "pre_screened_out": 0,
            "fanout_state": "claimed",
        },
        started_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db.add(durable)
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        applications_routes, "_claim_batch_queue", lambda _role_id: None
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_terminal_counts",
        lambda *_args, **_kwargs: (0, 0, 0),
    )

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    db.refresh(durable)

    assert response["status"] == "failed"
    assert response["run_id"] == 940
    assert durable.status == "failed"
    assert durable.finished_at is not None


def test_cancel_recovers_and_marks_durable_run_without_local_or_redis_state(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="restart-cancel")
    durable = BackgroundJobRun(
        id=970,
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={"selected_total": 5, "fanout_complete": False},
        started_at=datetime.now(timezone.utc),
    )
    db.add(durable)
    db.commit()
    store: dict[int, dict] = {}
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(
        applications_routes,
        "_set_cancel_flag",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        applications_routes, "_clear_batch_queue", lambda _role_id: None
    )

    response = applications_routes.cancel_batch_score(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    db.refresh(durable)

    assert response["ok"] is True
    assert response["status"] == "cancelling"
    assert response["pending_jobs_cancelled"] == 0
    assert store[role.id]["run_id"] == 970
    assert store[role.id]["status"] == "cancelling"
    assert durable.status == "cancelling"
    assert durable.cancel_requested_at is not None


def test_status_rejects_overlarge_not_enqueued_instead_of_false_completion(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="invalid-terminal-count")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Exact target",
        applied_at=datetime.now(timezone.utc),
    )
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [application.id],
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
            "fanout_complete": True,
            "not_enqueued": 2,
        },
    )
    db.add(run)
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    db.refresh(run)
    assert response["status"] == "failed"
    assert response["errors"] == 1
    assert run.status == "failed"
    assert run.error == "scoring_batch_invalid_terminal_receipts"


def test_stored_completed_receipt_deficit_fails_without_starting_successor(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="stored-completed-deficit")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Missing terminal receipt",
        applied_at=datetime.now(timezone.utc),
    )
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [application.id],
            "dispatched_application_ids": [application.id],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
            "fanout_complete": True,
            "queued_successor": {
                "queue_id": "must-not-start",
                "include_scored": False,
                "applied_after": None,
                "state": "pending",
                "dispatch_attempt": 0,
            },
        },
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        scoring_batch_runtime,
        "claim_scoring_successor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incomplete terminal batch claimed its successor")
        ),
    )

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    db.refresh(run)
    assert response["status"] == "failed"
    assert run.status == "failed"
    assert run.error == "scoring_batch_incomplete_terminal_receipts"
    assert "queued_successor" in dict(run.counters)


def test_status_rejects_terminal_job_for_a_different_undispatched_target(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="terminal-identity")
    dispatched = _add_application(
        db,
        organization=organization,
        role=role,
        label="Dispatched target",
        applied_at=datetime.now(timezone.utc),
    )
    other = _add_application(
        db,
        organization=organization,
        role=role,
        label="Not-enqueued target",
        applied_at=datetime.now(timezone.utc),
    )
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={
            "total": 2,
            "selected_total": 2,
            "target_application_ids": [dispatched.id, other.id],
            "dispatched_application_ids": [dispatched.id],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
            "fanout_complete": True,
            "not_enqueued": 1,
        },
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    _add_score_job(
        db,
        application=other,
        status=SCORE_JOB_DONE,
        batch_run_id=run.id,
    )
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    db.refresh(run)
    assert response["status"] == "failed"
    assert run.status == "failed"
    assert run.error == "scoring_batch_invalid_terminal_receipts"


def test_active_external_receipt_blocks_terminal_status_and_successor_claim(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="active-associated")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Associated",
        applied_at=datetime.now(timezone.utc),
    )
    job = _add_score_job(db, application=application, status=SCORE_JOB_PENDING)
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [application.id],
            "dispatched_application_ids": [application.id],
            "score_job_ids": [job.id],
            "owned_score_job_ids": [],
            "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
            "fanout_complete": True,
            "queued_successor": {
                "queue_id": "after-active",
                "include_scored": False,
                "applied_after": None,
                "state": "pending",
            },
        },
        started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    claims: list[int] = []
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        scoring_batch_runtime,
        "claim_scoring_successor",
        lambda run_id, **_kwargs: claims.append(run_id),
    )

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "running"
    assert response["run_id"] == run.id
    assert response["queued"] == {"include_scored": False}
    assert claims == []


def test_start_persists_successor_when_redis_is_unavailable(db, monkeypatch) -> None:
    organization, role = _seed_batch_scope(db, label="durable-queue")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [],
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        applications_routes,
        "_write_batch_queue",
        lambda *_args, **_kwargs: False,
    )

    response = applications_routes.batch_score_role(
        role.id,
        include_scored=True,
        applied_after=None,
        dry_run=False,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    db.expire(run, ["counters"])

    assert response["status"] == "queued"
    queued = dict(run.counters)["queued_successor"]
    assert queued["include_scored"] is True
    assert queued["state"] == "pending"
    assert queued["queue_id"]


def test_cancel_updates_only_jobs_owned_by_the_exact_batch(db, monkeypatch) -> None:
    organization, role = _seed_batch_scope(db, label="owned-cancel")
    owned_app = _add_application(
        db,
        organization=organization,
        role=role,
        label="Owned",
        applied_at=datetime.now(timezone.utc),
    )
    external_app = _add_application(
        db,
        organization=organization,
        role=role,
        label="External",
        applied_at=datetime.now(timezone.utc),
    )
    non_target_app = _add_application(
        db,
        organization=organization,
        role=role,
        label="Misbound non-target",
        applied_at=datetime.now(timezone.utc),
    )
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={},
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    owned = _add_score_job(
        db,
        application=owned_app,
        status=SCORE_JOB_PENDING,
        batch_run_id=run.id,
    )
    external = _add_score_job(
        db,
        application=external_app,
        status=SCORE_JOB_PENDING,
    )
    misbound = _add_score_job(
        db,
        application=non_target_app,
        status=SCORE_JOB_PENDING,
        batch_run_id=run.id,
    )
    run.counters = {
        "total": 2,
        "selected_total": 2,
        "target_application_ids": [owned_app.id, external_app.id],
        "dispatched_application_ids": [owned_app.id, external_app.id],
        "score_job_ids": [owned.id, external.id],
        "owned_score_job_ids": [owned.id],
        "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
    }
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        applications_routes, "_set_cancel_flag", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        applications_routes, "_clear_batch_queue", lambda _role_id: None
    )

    response = applications_routes.cancel_batch_score(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    db.refresh(owned)
    db.refresh(external)
    db.refresh(misbound)
    db.refresh(run)

    assert response["ok"] is True
    assert response["pending_jobs_cancelled"] == 1
    assert owned.status == SCORE_JOB_ERROR
    assert owned.error_message == "cancelled_by_recruiter"
    assert external.status == SCORE_JOB_PENDING
    assert misbound.status == SCORE_JOB_PENDING
    assert run.status == "cancelling"
    assert run.cancel_requested_at is not None


def test_exact_counts_use_latest_associated_attempt_per_application(db) -> None:
    organization, role = _seed_batch_scope(db, label="latest-receipt")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Recovered",
        applied_at=datetime.now(timezone.utc),
    )
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={},
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    failed = _add_score_job(
        db,
        application=application,
        status=SCORE_JOB_ERROR,
        batch_run_id=run.id,
    )
    recovered = _add_score_job(
        db,
        application=application,
        status=SCORE_JOB_DONE,
        batch_run_id=run.id,
    )
    unrelated = _add_score_job(
        db,
        application=application,
        status=SCORE_JOB_ERROR,
    )
    db.commit()

    counts = scoring_batch_state.scoring_batch_exact_terminal_counts(
        db,
        run_id=run.id,
        progress={"score_job_ids": [failed.id, recovered.id]},
    )

    assert counts == (1, 0, 0)
    assert unrelated.id > recovered.id


def test_scoring_successor_helpers_reject_cross_scope_receipts(db) -> None:
    organization, role = _seed_batch_scope(db, label="successor-scope")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()

    assert not scoring_batch_successors.queue_scoring_successor(
        run.id,
        role_id=role.id + 1,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="scoped",
    )
    assert scoring_batch_successors.queue_scoring_successor(
        run.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="scoped",
    )
    assert (
        scoring_batch_successors.claim_scoring_successor(
            run.id,
            role_id=role.id,
            organization_id=organization.id + 1,
        )
        is None
    )
    claimed = scoring_batch_successors.claim_scoring_successor(
        run.id,
        role_id=role.id,
        organization_id=organization.id,
    )
    assert claimed is not None
    assert not scoring_batch_successors.complete_scoring_successor(
        run.id,
        role_id=role.id + 1,
        organization_id=organization.id,
        queue_id="scoped",
        claim_token=claimed["claim_token"],
    )
    assert scoring_batch_successors.complete_scoring_successor(
        run.id,
        role_id=role.id,
        organization_id=organization.id,
        queue_id="scoped",
        claim_token=claimed["claim_token"],
    )


def test_zero_target_durable_successor_is_consumed_without_dispatch(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="zero-successor")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [999],
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "not_enqueued": 1,
            "queue_contract": scoring_batch_state.SCORING_DURABLE_QUEUE_CONTRACT,
            "queued_successor": {
                "queue_id": "no-targets",
                "include_scored": False,
                "applied_after": None,
                "state": "pending",
                "dispatch_attempt": 0,
            },
        },
        started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    _allow_role(monkeypatch, role)
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("zero-target successor dispatched")
        ),
    )

    response = applications_routes.batch_score_status(
        role.id,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    db.expire(run, ["counters"])

    assert response["status"] == "completed"
    assert response["queued"] is None
    assert "queued_successor" not in dict(run.counters)


def test_ambiguous_successor_create_converges_to_existing_child(db) -> None:
    organization, role = _seed_batch_scope(db, label="successor-dedupe")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Target",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    assert scoring_batch_successors.queue_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="ambiguous",
    )
    claimed = scoring_batch_successors.claim_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
    )
    assert claimed is not None
    child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        dispatch_key=(f"scoring-batch:{organization.id}:{role.id}:ambiguous:0"),
        counters={
            "successor_queue_id": "ambiguous",
            "target_application_ids": [application.id],
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(child)
    db.commit()

    result = scoring_batch_successor_reconcile.dispatch_claimed_scoring_successor(
        db,
        parent_run_id=parent.id,
        role_id=role.id,
        organization_id=organization.id,
        claimed=claimed,
        create_run_fn=lambda **_kwargs: None,
    )
    db.expire(parent, ["counters"])

    assert result["outcome"] == "deduplicated"
    assert result["run_id"] == child.id
    assert "queued_successor" not in dict(parent.counters)


def test_cancel_after_successor_claim_revokes_dispatch_before_child_creation(
    db,
) -> None:
    organization, role = _seed_batch_scope(db, label="successor-cancel-fence")
    _add_application(
        db,
        organization=organization,
        role=role,
        label="Cancelled successor target",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    assert scoring_batch_successors.queue_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="cancelled-claim",
    )
    claimed = scoring_batch_successors.claim_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
    )
    assert claimed is not None
    db.refresh(parent)
    parent.cancel_requested_at = datetime.now(timezone.utc)
    parent.status = "cancelling"
    counters = dict(parent.counters)
    counters.pop("queued_successor")
    parent.counters = counters
    db.commit()

    result = scoring_batch_successor_reconcile.dispatch_claimed_scoring_successor(
        db,
        parent_run_id=parent.id,
        role_id=role.id,
        organization_id=organization.id,
        claimed=claimed,
        create_run_fn=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cancelled successor child was created")
        ),
    )

    assert result["outcome"] == "revoked"
    assert result["reason"] == "successor_claim_not_authorized"


def test_ambiguous_successor_retries_keep_one_stable_dispatch_key(db) -> None:
    organization, role = _seed_batch_scope(db, label="successor-stable-key")
    _add_application(
        db,
        organization=organization,
        role=role,
        label="Stable target",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    assert scoring_batch_successors.queue_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="stable-ambiguous",
    )
    observed_keys: list[str] = []

    def ambiguous_create(**kwargs):
        observed_keys.append(str(kwargs["dispatch_key"]))
        return None

    for _ in range(2):
        claimed = scoring_batch_successors.claim_scoring_successor(
            parent.id,
            role_id=role.id,
            organization_id=organization.id,
        )
        assert claimed is not None
        result = scoring_batch_successor_reconcile.dispatch_claimed_scoring_successor(
            db,
            parent_run_id=parent.id,
            role_id=role.id,
            organization_id=organization.id,
            claimed=claimed,
            create_run_fn=ambiguous_create,
        )
        assert result["outcome"] == "released"

    expected_key = f"scoring-batch:{organization.id}:{role.id}:stable-ambiguous:0"
    assert observed_keys == [expected_key, expected_key]


def test_successor_dispatch_key_conflict_is_quarantined(db) -> None:
    organization, role = _seed_batch_scope(db, label="successor-key-conflict")
    _add_application(
        db,
        organization=organization,
        role=role,
        label="Conflict target",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    queue_id = "conflicting-key"
    assert scoring_batch_successors.queue_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id=queue_id,
    )
    claimed = scoring_batch_successors.claim_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
    )
    assert claimed is not None
    dispatch_key = f"scoring-batch:{organization.id}:{role.id}:{queue_id}:0"
    db.add(
        BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role.id,
            organization_id=organization.id,
            status="failed",
            dispatch_key=dispatch_key,
            counters={"successor_queue_id": "some-other-intent"},
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    result = scoring_batch_successor_reconcile.dispatch_claimed_scoring_successor(
        db,
        parent_run_id=parent.id,
        role_id=role.id,
        organization_id=organization.id,
        claimed=claimed,
        create_run_fn=lambda **_kwargs: None,
    )
    db.expire(parent, ["counters"])

    assert result["outcome"] == "invalid"
    counters = dict(parent.counters)
    assert "queued_successor" not in counters
    assert counters["quarantined_scoring_successor"]["dispatch_key"] == dispatch_key
    assert counters["quarantined_scoring_successor"]["reason"] == (
        "successor_dispatch_key_conflict"
    )


def test_successor_broker_failure_keeps_one_child_for_recovery(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="successor-recovery")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Successor Target",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    assert scoring_batch_successors.queue_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="recover-one-child",
    )
    claimed = scoring_batch_successors.claim_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
    )
    assert claimed is not None

    def _broker_failure(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(scoring_tasks.batch_score_role, "delay", _broker_failure)

    result = scoring_batch_successor_reconcile.dispatch_claimed_scoring_successor(
        db,
        parent_run_id=parent.id,
        role_id=role.id,
        organization_id=organization.id,
        claimed=claimed,
    )

    db.expire_all()
    child = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.scope_id == role.id,
            BackgroundJobRun.id != parent.id,
        )
        .one()
    )
    assert result["outcome"] == "recovery_pending"
    assert result["run_id"] == child.id
    assert child.status == "dispatching"
    assert child.finished_at is None
    assert child.counters["target_application_ids"] == [application.id]
    assert child.counters["fanout_last_publish_error"] == "broker_publish_failed"
    db.refresh(parent)
    assert "queued_successor" not in dict(parent.counters)


def test_bounded_successor_reconciliation_starts_without_browser_poll(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="successor-beat")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Beat Target",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={},
        started_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.flush()
    completed_job = _add_score_job(
        db,
        application=application,
        status=SCORE_JOB_DONE,
        batch_run_id=parent.id,
    )
    parent.counters = {
        "total": 1,
        "selected_total": 1,
        "target_application_ids": [application.id],
        "dispatched_application_ids": [application.id],
        "score_job_ids": [completed_job.id],
        "owned_score_job_ids": [completed_job.id],
        "fanout_complete": True,
    }
    db.commit()
    assert scoring_batch_successors.queue_scoring_successor(
        parent.id,
        role_id=role.id,
        organization_id=organization.id,
        include_scored=False,
        applied_after=None,
        queue_id="beat-start",
    )
    dispatched: list[dict] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **kwargs: dispatched.append(kwargs),
    )

    result = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=5
    )
    db.expire(parent, ["counters"])
    db.refresh(parent)

    assert result["started"] == 1
    assert len(dispatched) == 1
    assert dispatched[0]["run_id"] > parent.id
    assert "queued_successor" not in dict(parent.counters)
    assert parent.status == "completed"
    assert parent.finished_at is not None


def test_backfill_binds_exact_targets_parent_and_child_run_ids(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="backfill-bind")
    before = _add_application(
        db,
        organization=organization,
        role=role,
        label="Before Backfill",
        applied_at=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
    )
    after = _add_application(
        db,
        organization=organization,
        role=role,
        label="After Backfill",
        applied_at=datetime(2026, 7, 1, 0, 1, tzinfo=timezone.utc),
    )
    db.commit()
    created: list[dict] = []
    run_ids = iter((1200, 1201))

    def _create(**kwargs):
        created.append(kwargs)
        return next(run_ids)

    dispatched: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(applications_routes, "_create_job_run", _create)
    monkeypatch.setattr(
        applications_routes, "_update_job_run", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        applications_routes, "_write_batch_meta", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(applications_routes, "_redis_client", lambda: None)
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    response = applications_routes.batch_score_all_roles(
        applied_after="2026-07-01T00:00:00Z",
        include_scored=False,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "dispatched"
    assert response["parent_run_id"] == 1200
    assert created[0]["scope_kind"] == applications_routes.SCOPE_KIND_ORG
    child_counters = created[1]["counters"]
    assert child_counters["target_application_ids"] == [after.id]
    assert before.id not in child_counters["target_application_ids"]
    assert child_counters["backfill_parent_run_id"] == 1200
    assert child_counters["score_job_ids"] == []
    assert dispatched == [
        (
            (role.id,),
            {
                "include_scored": False,
                "applied_after": "2026-07-01T00:00:00Z",
                "run_id": 1201,
            },
        )
    ]


def test_backfill_broker_failure_remains_durable_and_recoverable(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="backfill-recovery")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Backfill Recovery Target",
        applied_at=datetime.now(timezone.utc),
    )
    db.commit()

    def _create(**kwargs):
        run = BackgroundJobRun(**kwargs)
        db.add(run)
        db.commit()
        return int(run.id)

    def _broker_failure(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(applications_routes, "_create_job_run", _create)
    monkeypatch.setattr(
        applications_routes, "_write_batch_meta", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(applications_routes, "_redis_client", lambda: None)
    monkeypatch.setattr(scoring_tasks.batch_score_role, "delay", _broker_failure)

    response = applications_routes.batch_score_all_roles(
        applied_after=None,
        include_scored=False,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    db.expire_all()
    child = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.scope_id == role.id,
        )
        .one()
    )
    parent = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.scope_kind == applications_routes.SCOPE_KIND_ORG,
            BackgroundJobRun.scope_id == organization.id,
        )
        .one()
    )
    assert response["status"] == "dispatched"
    assert response["dispatched"][0]["dispatch_status"] == "recovery_pending"
    assert response["dispatched"][0]["run_id"] == child.id
    assert child.status == "dispatching"
    assert child.finished_at is None
    assert child.counters["target_application_ids"] == [application.id]
    assert child.counters["fanout_last_publish_error"] == "broker_publish_failed"
    assert parent.status == "running"
    assert parent.finished_at is None


def test_backfill_status_recovers_from_durable_parent_without_redis(
    db,
    monkeypatch,
) -> None:
    organization, role = _seed_batch_scope(db, label="backfill-restart")
    application = _add_application(
        db,
        organization=organization,
        role=role,
        label="Backfill Done",
        applied_at=datetime.now(timezone.utc),
    )
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=applications_routes.SCOPE_KIND_ORG,
        scope_id=organization.id,
        organization_id=organization.id,
        status="running",
        counters={},
        started_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.flush()
    child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(child)
    db.flush()
    job = _add_score_job(
        db,
        application=application,
        status=SCORE_JOB_DONE,
        batch_run_id=child.id,
    )
    child.counters = {
        "total": 1,
        "selected_total": 1,
        "target_application_ids": [application.id],
        "dispatched_application_ids": [application.id],
        "score_job_ids": [job.id],
        "owned_score_job_ids": [job.id],
        "fanout_complete": True,
    }
    parent.counters = {
        "backfill_parent": True,
        "applied_after": None,
        "children": [
            {
                "role_id": role.id,
                "run_id": child.id,
                "target": 1,
                "dispatch_status": "dispatched",
            }
        ],
        "skipped": [],
        "total_target": 1,
        "fanout_complete": True,
    }
    db.commit()
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(applications_routes, "_redis_client", lambda: None)

    response = applications_routes.batch_score_all_status(
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "completed"
    assert response["parent_run_id"] == parent.id
    assert response["total_scored"] == 1
    assert response["total_errors"] == 0
    assert response["roles"][0]["run_id"] == child.id


def test_receipt_chunks_stay_below_database_bind_limits() -> None:
    chunks = list(scoring_batch_state._chunks(tuple(range(1, 1_202))))

    assert [len(chunk) for chunk in chunks] == [500, 500, 201]
