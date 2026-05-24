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
from app.tasks import assessment_tasks
from app.tasks.workable_tasks import (
    _STUCK_DECISION_BATCH_TIMEOUT_MINUTES,
    expire_stuck_decision_batches,
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
    assert "worker likely killed" in (reaped.error or "")


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


def test_sync_mutex_keeps_static_ttl_and_no_heartbeat():
    client = _fake_redis()
    with patch("redis.Redis.from_url", return_value=client):
        handle = assessment_tasks._acquire_workable_org_mutex(9, source="starred")
    _, ex_kwarg = client.set.call_args
    assert ex_kwarg["ex"] == assessment_tasks._WORKABLE_ORG_MUTEX_TTL_SECONDS
    assert handle[2] is None  # no heartbeat for sync callers


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
