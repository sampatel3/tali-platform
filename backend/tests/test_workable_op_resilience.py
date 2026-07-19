"""Deploy-resilience for the Workable op write path.

A worker SIGKILLed mid-approve-batch (deploy) skips run_workable_op_task's
finally block, leaving decisions stranded in 'processing', the decision_batch
BackgroundJobRun stuck in 'running', and the per-org Redis mutex leaked. These
cover the three recovery mechanisms:

  * expire_stuck_decision_batches (watchdog) — returns stranded decisions to
    the Hub queue and fails the stuck job run.
  * the op-path mutex heartbeat / short TTL — auto-expires a leaked lock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.agent_decision import AgentDecision
from app.models.background_job_run import (
    JOB_KIND_DECISION_BATCH,
    JOB_KIND_WORKABLE_OP,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.background_job_runs import SCOPE_KIND_ORG
from app.services import background_job_runs
from app.services.workable_op_runner import (
    OP_OVERRIDE_DECISION,
    AtsJobRunPersistenceError,
    enqueue_workable_op,
)
from app.tasks import assessment_tasks
from app.tasks.workable_tasks import (
    _STUCK_DECISION_BATCH_TIMEOUT_MINUTES,
    _STUCK_OVERRIDE_TIMEOUT_MINUTES,
    expire_stuck_decision_batches,
    expire_stuck_override_ops,
    run_workable_op_task,
)


def _seed(db):
    org = Organization(name="O", slug=f"o-res-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role)
    db.flush()
    return org, role


def _add_processing_decision(db, org, role, *, status="processing"):
    cand = Candidate(organization_id=org.id, email=f"c{id(object())}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="skip_assessment_reject",
        recommendation="skip_assessment_reject",
        status=status,
        reasoning="x",
        evidence={},
        model_version="pre_screen_v1",
        prompt_version="pre_screen_threshold.v1",
        idempotency_key=f"pre_screen_reject:{int(app.id)}",
        active_capabilities={},
        token_spend={},
    )
    db.add(decision)
    db.flush()
    return decision


def _make_run(db, org, *, kind, status, age_minutes, decision_ids):
    run = BackgroundJobRun(
        kind=kind,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        status=status,
        counters={"total": len(decision_ids), "decision_ids": decision_ids},
        started_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )
    db.add(run)
    db.flush()
    return run


def _make_override_run(db, org, decision, *, status, age_minutes):
    run = BackgroundJobRun(
        kind=JOB_KIND_WORKABLE_OP,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        status=status,
        counters={
            "op_type": OP_OVERRIDE_DECISION,
            "decision_id": int(decision.id),
        },
        started_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )
    db.add(run)
    db.flush()
    return run


# --- watchdog ---------------------------------------------------------------


def test_watchdog_requeues_stranded_batch_and_fails_run(db):
    org, role = _seed(db)
    d1 = _add_processing_decision(db, org, role)
    d2 = _add_processing_decision(db, org, role)
    run = _make_run(
        db,
        org,
        kind=JOB_KIND_DECISION_BATCH,
        status="running",
        age_minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES + 5,
        decision_ids=[int(d1.id), int(d2.id)],
    )
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["status"] == "ok"
    assert out["failed_run_count"] == 1
    assert out["requeued_decision_count"] == 2
    db.expire_all()
    assert db.query(AgentDecision).get(d1.id).status == "pending"
    assert db.query(AgentDecision).get(d2.id).status == "pending"
    assert "watchdog" in (db.query(AgentDecision).get(d1.id).resolution_note or "")
    reaped = db.query(BackgroundJobRun).get(run.id)
    assert reaped.status == "failed"
    assert reaped.finished_at is not None
    assert "stuck in 'running'" in (reaped.error or "")


def test_watchdog_requeues_stranded_queued_batch(db):
    """A batch that died in the lock-wait re-enqueue loop never reaches
    'running' — it stays 'queued' with its decisions stranded in 'processing'.
    The watchdog must reap that state too (regression: it only reaped 'running',
    so a queued-state death stranded the batch forever)."""
    org, role = _seed(db)
    d1 = _add_processing_decision(db, org, role)
    d2 = _add_processing_decision(db, org, role)
    run = _make_run(
        db,
        org,
        kind=JOB_KIND_DECISION_BATCH,
        status="queued",
        age_minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES + 5,
        decision_ids=[int(d1.id), int(d2.id)],
    )
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["failed_run_count"] == 1
    assert out["requeued_decision_count"] == 2
    db.expire_all()
    assert db.query(AgentDecision).get(d1.id).status == "pending"
    assert db.query(AgentDecision).get(d2.id).status == "pending"
    reaped = db.query(BackgroundJobRun).get(run.id)
    assert reaped.status == "failed"
    assert reaped.finished_at is not None
    assert "stuck in 'queued'" in (reaped.error or "")


def test_watchdog_ignores_fresh_queued_batch(db):
    """A queued batch still inside its lock-wait window is healthily waiting out
    a concurrent Workable write — don't reap it."""
    org, role = _seed(db)
    d1 = _add_processing_decision(db, org, role)
    _make_run(
        db,
        org,
        kind=JOB_KIND_DECISION_BATCH,
        status="queued",
        age_minutes=1,
        decision_ids=[int(d1.id)],
    )
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["failed_run_count"] == 0
    db.expire_all()
    assert db.query(AgentDecision).get(d1.id).status == "processing"


def test_watchdog_ignores_fresh_running_batch(db):
    org, role = _seed(db)
    d1 = _add_processing_decision(db, org, role)
    _make_run(
        db,
        org,
        kind=JOB_KIND_DECISION_BATCH,
        status="running",
        age_minutes=1,  # well within the timeout — a healthy in-flight batch
        decision_ids=[int(d1.id)],
    )
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["failed_run_count"] == 0
    db.expire_all()
    assert db.query(AgentDecision).get(d1.id).status == "processing"


def test_watchdog_idempotent_skips_already_resolved_decision(db):
    org, role = _seed(db)
    # The batch resolved this one before dying; only the run row is stale.
    d_done = _add_processing_decision(db, org, role, status="approved")
    run = _make_run(
        db,
        org,
        kind=JOB_KIND_DECISION_BATCH,
        status="running",
        age_minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES + 5,
        decision_ids=[int(d_done.id)],
    )
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["requeued_decision_count"] == 0
    assert out["failed_run_count"] == 1
    db.expire_all()
    # Not dragged back to pending — the writeback already landed.
    assert db.query(AgentDecision).get(d_done.id).status == "approved"
    assert db.query(BackgroundJobRun).get(run.id).status == "failed"


def test_watchdog_leaves_single_workable_op_runs_alone(db):
    """Single ops retry with backoff and can be legitimately 'running' for
    hours — reaping them here would false-fail a healthy retry."""
    org, role = _seed(db)
    d1 = _add_processing_decision(db, org, role)
    run = _make_run(
        db,
        org,
        kind=JOB_KIND_WORKABLE_OP,
        status="running",
        age_minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES + 60,
        decision_ids=[int(d1.id)],
    )
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["failed_run_count"] == 0
    db.expire_all()
    assert db.query(BackgroundJobRun).get(run.id).status == "running"
    assert db.query(AgentDecision).get(d1.id).status == "processing"


# --- non-replayable override delivery compensation -------------------------


def test_override_initial_broker_rejection_requeues_and_fails_run(db, monkeypatch):
    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role)
    db.commit()

    def _reject_publish(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(run_workable_op_task, "apply_async", _reject_publish)

    run_id = enqueue_workable_op(
        organization_id=int(org.id),
        op_type=OP_OVERRIDE_DECISION,
        payload={
            "decision_id": int(decision.id),
            "override_action": "advance",
        },
    )

    assert run_id is not None
    db.expire_all()
    restored = db.get(AgentDecision, int(decision.id))
    run = db.get(BackgroundJobRun, int(run_id))
    assert restored.status == "pending"
    assert "could not be delivered" in (restored.resolution_note or "")
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.counters["op_type"] == OP_OVERRIDE_DECISION
    assert run.counters["decision_id"] == int(decision.id)
    # The non-replayable payload is never persisted for recovery.
    assert "recovery_payload" not in run.counters


def test_ats_op_is_never_published_without_durable_job_run(monkeypatch):
    with patch("app.services.background_job_runs.create_run", return_value=None), patch.object(
        run_workable_op_task, "apply_async"
    ) as publish:
        with pytest.raises(AtsJobRunPersistenceError):
            enqueue_workable_op(
                organization_id=123,
                op_type=OP_OVERRIDE_DECISION,
                payload={"decision_id": 456, "override_action": "advance"},
            )

    publish.assert_not_called()


def test_background_job_creation_does_not_depend_on_post_commit_refresh(db):
    org, _role = _seed(db)
    db.commit()

    with patch(
        "sqlalchemy.orm.Session.refresh",
        side_effect=AssertionError("post-commit refresh must not run"),
    ):
        run_id = background_job_runs.create_run(
            kind=JOB_KIND_WORKABLE_OP,
            scope_kind=SCOPE_KIND_ORG,
            scope_id=int(org.id),
            organization_id=int(org.id),
            counters={"op_type": OP_OVERRIDE_DECISION},
            status="queued",
        )

    assert isinstance(run_id, int) and run_id > 0
    db.expire_all()
    assert db.get(BackgroundJobRun, run_id) is not None


def test_untracked_worker_delivery_is_refused_before_provider_work(db):
    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role)
    db.commit()

    with patch("app.services.workable_op_runner.execute_op") as execute:
        result = run_workable_op_task.run(
            job_run_id=None,
            organization_id=int(org.id),
            op_type=OP_OVERRIDE_DECISION,
            payload={"decision_id": int(decision.id), "override_action": "advance"},
        )

    assert result == {
        "status": "failed",
        "op_type": OP_OVERRIDE_DECISION,
        "code": "job_run_persistence_failed",
    }
    execute.assert_not_called()
    db.expire_all()
    assert db.get(AgentDecision, int(decision.id)).status == "pending"


@pytest.mark.parametrize("mismatch", ["organization", "op_type"])
def test_worker_refuses_tracking_row_that_does_not_match_delivery(db, mismatch):
    org, _role = _seed(db)
    other_org = Organization(name="Other", slug=f"other-tracking-{id(org)}")
    db.add(other_org)
    db.commit()
    run_id = background_job_runs.create_run(
        kind=JOB_KIND_WORKABLE_OP,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        counters={"op_type": OP_OVERRIDE_DECISION},
        status="queued",
    )
    assert run_id is not None
    organization_id = int(other_org.id) if mismatch == "organization" else int(org.id)
    op_type = OP_OVERRIDE_DECISION if mismatch == "organization" else "move_stage"

    with patch("app.services.workable_op_runner.execute_op") as execute:
        result = run_workable_op_task.run(
            job_run_id=int(run_id),
            organization_id=organization_id,
            op_type=op_type,
            payload={},
        )

    assert result["status"] == "already_terminal"
    execute.assert_not_called()
    db.expire_all()
    assert db.get(BackgroundJobRun, int(run_id)).status == "queued"


def test_approve_batch_requeues_when_job_tracking_cannot_be_created(db):
    from app.actions import approve_decision as approve_decision_action
    from app.actions.types import ACTOR_RECRUITER, Actor

    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role, status="pending")
    db.commit()

    with patch(
        "app.services.background_job_runs.add_run",
        side_effect=RuntimeError("tracking unavailable"),
    ), patch.object(run_workable_op_task, "apply_async") as publish:
        with pytest.raises(AtsJobRunPersistenceError):
            approve_decision_action.enqueue_batch(
                db,
                Actor(type=ACTOR_RECRUITER, user_id=None),
                organization_id=int(org.id),
                decision_ids=[int(decision.id)],
            )

    db.expire_all()
    restored = db.get(AgentDecision, int(decision.id))
    assert restored.status == "pending"
    assert restored.resolution_note is None
    assert (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.organization_id == int(org.id))
        .count()
        == 0
    )
    publish.assert_not_called()


def test_override_requeues_when_job_tracking_cannot_be_created(db):
    from app.actions import override_decision as override_decision_action
    from app.actions.types import ACTOR_RECRUITER, Actor

    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role, status="pending")
    db.commit()

    with patch("app.services.background_job_runs.create_run", return_value=None):
        with pytest.raises(AtsJobRunPersistenceError):
            override_decision_action.enqueue(
                db,
                Actor(type=ACTOR_RECRUITER, user_id=None),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                override_action="advance",
            )

    db.expire_all()
    restored = db.get(AgentDecision, int(decision.id))
    assert restored.status == "pending"
    assert "No provider update was sent" in (restored.resolution_note or "")


@pytest.mark.parametrize("run_status", ["queued", "running"])
def test_override_watchdog_requeues_stale_delivery(db, run_status):
    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role)
    run = _make_override_run(
        db,
        org,
        decision,
        status=run_status,
        age_minutes=_STUCK_OVERRIDE_TIMEOUT_MINUTES + 5,
    )
    db.commit()

    out = expire_stuck_override_ops()

    assert out["failed_run_count"] == 1
    assert out["requeued_decision_count"] == 1
    db.expire_all()
    assert db.get(AgentDecision, int(decision.id)).status == "pending"
    terminal = db.get(BackgroundJobRun, int(run.id))
    assert terminal.status == "failed"
    assert terminal.finished_at is not None
    assert terminal.counters["failure_code"] == "stale_delivery"


def test_override_watchdog_ignores_fresh_delivery(db):
    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role)
    run = _make_override_run(db, org, decision, status="queued", age_minutes=1)
    db.commit()

    out = expire_stuck_override_ops()

    assert out["failed_run_count"] == 0
    db.expire_all()
    assert db.get(AgentDecision, int(decision.id)).status == "processing"
    assert db.get(BackgroundJobRun, int(run.id)).status == "queued"


def test_override_watchdog_terminalizes_run_without_reopening_resolved_decision(db):
    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role, status="approved")
    run = _make_override_run(
        db,
        org,
        decision,
        status="running",
        age_minutes=_STUCK_OVERRIDE_TIMEOUT_MINUTES + 5,
    )
    db.commit()

    first = expire_stuck_override_ops()
    second = expire_stuck_override_ops()

    assert first["failed_run_count"] == 1
    assert first["requeued_decision_count"] == 0
    assert second["failed_run_count"] == 0
    db.expire_all()
    assert db.get(AgentDecision, int(decision.id)).status == "approved"
    assert db.get(BackgroundJobRun, int(run.id)).status == "failed"


def test_override_lock_wait_publish_rejection_compensates(db, monkeypatch):
    org, role = _seed(db)
    decision = _add_processing_decision(db, org, role)
    run = _make_override_run(db, org, decision, status="queued", age_minutes=0)
    db.commit()

    monkeypatch.setattr(
        assessment_tasks,
        "_acquire_workable_org_mutex",
        lambda *args, **kwargs: None,
    )

    def _reject_publish(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(run_workable_op_task, "apply_async", _reject_publish)

    out = run_workable_op_task.run(
        job_run_id=int(run.id),
        organization_id=int(org.id),
        op_type=OP_OVERRIDE_DECISION,
        payload={
            "decision_id": int(decision.id),
            "override_action": "advance",
        },
        lock_attempt=0,
    )

    assert out["status"] == "delivery_compensated"
    assert out["requeued"] is True
    db.expire_all()
    assert db.get(AgentDecision, int(decision.id)).status == "pending"
    assert db.get(BackgroundJobRun, int(run.id)).status == "failed"


# --- op-path mutex heartbeat / short TTL ------------------------------------


def _fake_redis(set_result=True):
    client = MagicMock()
    client.set.return_value = set_result
    return client


def test_op_mutex_uses_short_ttl_and_spawns_heartbeat():
    client = _fake_redis()
    with patch("redis.Redis.from_url", return_value=client):
        handle = assessment_tasks._acquire_workable_org_mutex(
            7, source="workable_op:approve_decisions", heartbeat=True
        )
    assert handle is not None and handle is not False
    _, ex_kwarg = client.set.call_args
    assert ex_kwarg["nx"] is True
    assert ex_kwarg["ex"] == assessment_tasks._WORKABLE_OP_MUTEX_TTL_SECONDS
    # Heartbeat enabled → a stop event rides along on the handle.
    assert handle[2] is not None

    assessment_tasks._release_workable_org_mutex(handle)
    assert handle[2].is_set()  # heartbeat told to stop
    client.delete.assert_called_once_with(handle[1])


def test_sync_tasks_acquire_mutex_with_heartbeat(db, monkeypatch):
    """All four Workable sync tasks must hold the per-org mutex with a heartbeat
    (short TTL + renew-while-alive), same as the op path.

    Regression for the 2026-05-24 incident: the sync path acquired with the
    static 30-min TTL and no heartbeat, so a worker SIGKILLed mid-sync (deploy)
    leaked the lock for up to 30 min. That starved every decision approve batch
    (whose lock-wait window is only ~10 min), failing bulk approvals with
    "Workable lock timeout" / watchdog "stuck in queued" regardless of size.
    """
    org = Organization(
        name="HB Org",
        slug=f"hb-org-{id(db)}",
        workable_connected=True,
        workable_access_token="tk",
        workable_subdomain="hb",
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    db.add_all(
        [
            Role(
                organization_id=org.id, name="Starred", source="workable",
                workable_job_id="STAR1", starred_for_auto_sync=True,
            ),
            Role(
                organization_id=org.id, name="Agent", source="workable",
                workable_job_id="AGENT1", agentic_mode_enabled=True, agent_paused_at=None,
            ),
            Role(
                organization_id=org.id, name="Plain", source="workable",
                workable_job_id="PLAIN1",
            ),
        ]
    )
    db.commit()

    captured: list[dict] = []

    def _spy_acquire(*args, **kwargs):
        captured.append(kwargs)
        return False  # "run unguarded" — exercises the call without real Redis

    class _FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def sync_org(self, *args, **kwargs):
            return {"jobs_seen": 0}

    monkeypatch.setattr(assessment_tasks.settings, "MVP_DISABLE_WORKABLE", False)
    monkeypatch.setattr(assessment_tasks, "_acquire_workable_org_mutex", _spy_acquire)
    # Hermetic: don't let ambient op-pending state in a shared Redis make a
    # sync task defer before it reaches the mutex acquire we're spying on.
    monkeypatch.setattr(assessment_tasks, "is_workable_op_pending", lambda *a, **kw: False)
    from app.components.integrations.workable import sync_service as sync_service_mod

    monkeypatch.setattr(sync_service_mod, "WorkableSyncService", _FakeService)

    assessment_tasks.sync_starred_roles.run()
    assessment_tasks.sync_workable_jobs.run()
    assessment_tasks.sync_agent_mode_roles.run()
    assessment_tasks.sync_workable_daily_candidates.run()

    assert len(captured) >= 4, (
        f"each sync task should acquire the mutex for the connected org, got {len(captured)}"
    )
    assert all(kw.get("heartbeat") is True for kw in captured), (
        f"every sync task must acquire the Workable mutex with heartbeat=True, got {captured}"
    )


def test_op_mutex_returns_none_when_held():
    client = _fake_redis(set_result=None)  # NX failed → held by another writer
    with patch("redis.Redis.from_url", return_value=client):
        handle = assessment_tasks._acquire_workable_org_mutex(
            3, source="workable_op:approve_decisions", heartbeat=True
        )
    assert handle is None


def test_heartbeat_renews_ttl_until_stopped():
    import threading
    import time

    client = MagicMock()
    stop = threading.Event()
    # ttl=3 → interval = max(1, min(40, 3//3)) = 1s; sleep past one beat.
    t = threading.Thread(
        target=assessment_tasks._workable_mutex_heartbeat,
        args=(client, "k", 3, stop),
        daemon=True,
    )
    t.start()
    time.sleep(1.2)
    stop.set()
    t.join(timeout=1)
    assert not t.is_alive()  # stop_event ends the loop promptly
    assert client.expire.call_count >= 1
    client.expire.assert_called_with("k", 3)
