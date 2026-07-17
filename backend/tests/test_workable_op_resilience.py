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
    OP_POST_NOTE,
    AtsJobRunPersistenceError,
    enqueue_workable_op,
)
from app.services.workable_actions_service import WorkableWritebackError
from app.tasks import assessment_tasks
from app.tasks.workable_tasks import (
    _STUCK_DECISION_BATCH_TIMEOUT_MINUTES,
    _STUCK_OVERRIDE_TIMEOUT_MINUTES,
    expire_stuck_decision_batches,
    expire_stuck_override_ops,
    recover_dispatching_workable_ops,
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


def _add_processing_decision(
    db,
    org,
    role,
    *,
    status="processing",
    decision_type="skip_assessment_reject",
):
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
        decision_type=decision_type,
        recommendation=decision_type,
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
    assert db.get(AgentDecision, d1.id).status == "pending"
    assert db.get(AgentDecision, d2.id).status == "pending"
    assert "watchdog" in (db.get(AgentDecision, d1.id).resolution_note or "")
    reaped = db.get(BackgroundJobRun, run.id)
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
    assert db.get(AgentDecision, d1.id).status == "pending"
    assert db.get(AgentDecision, d2.id).status == "pending"
    reaped = db.get(BackgroundJobRun, run.id)
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
    assert db.get(AgentDecision, d1.id).status == "processing"


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
    assert db.get(AgentDecision, d1.id).status == "processing"


def test_watchdog_uses_the_latest_running_attempt_after_a_long_lock_wait(db):
    org, role = _seed(db)
    d1 = _add_processing_decision(db, org, role)
    run = _make_run(
        db,
        org,
        kind=JOB_KIND_DECISION_BATCH,
        status="running",
        age_minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES + 5,
        decision_ids=[int(d1.id)],
    )
    run.counters = {
        **run.counters,
        "last_started_at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()

    out = expire_stuck_decision_batches()

    assert out["failed_run_count"] == 0
    db.expire_all()
    assert db.get(BackgroundJobRun, run.id).status == "running"
    assert db.get(AgentDecision, d1.id).status == "processing"


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
    assert db.get(AgentDecision, d_done.id).status == "approved"
    assert db.get(BackgroundJobRun, run.id).status == "failed"


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
    assert db.get(BackgroundJobRun, run.id).status == "running"
    assert db.get(AgentDecision, d1.id).status == "processing"


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


def test_confirmed_note_dispatch_key_collapses_crash_replay_before_publish(db):
    org, _role = _seed(db)
    db.commit()
    key = "chat-command/" + ("a" * 64)
    payload = {"application_id": 321, "user_id": 654, "body": "Review context"}

    with patch.object(run_workable_op_task, "apply_async") as publish:
        first = enqueue_workable_op(
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=payload,
            dispatch_key=key,
        )
        second = enqueue_workable_op(
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=payload,
            dispatch_key=key,
        )

    assert first == second
    publish.assert_called_once()
    db.expire_all()
    rows = db.query(BackgroundJobRun).filter_by(dispatch_key=key).all()
    assert [int(row.id) for row in rows] == [int(first)]


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


def test_ats_db_claim_rejects_running_duplicate_and_opens_only_for_retry(db):
    """Redis is an optimization: the durable row remains the side-effect lock."""

    org, _role = _seed(db)
    db.commit()
    run_id = background_job_runs.create_run(
        kind=JOB_KIND_WORKABLE_OP,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        counters={"op_type": OP_POST_NOTE},
        status="queued",
    )
    assert isinstance(run_id, int)

    claim_kwargs = {
        "organization_id": int(org.id),
        "expected_kind": JOB_KIND_WORKABLE_OP,
        "op_type": OP_POST_NOTE,
    }
    assert background_job_runs.claim_ats_run(run_id, **claim_kwargs) is True
    # This was previously True: two fail-open Redis deliveries could both call
    # the provider while the same durable run said ``running``.
    assert background_job_runs.claim_ats_run(run_id, **claim_kwargs) is False

    assert background_job_runs.release_ats_run_for_retry(
        run_id, delay_seconds=60
    ) is True
    # A duplicate delivery cannot bypass provider backoff merely because the
    # legitimate retry returned the row to ``queued``.
    assert background_job_runs.claim_ats_run(run_id, **claim_kwargs) is False

    db.expire_all()
    row = db.get(BackgroundJobRun, run_id)
    counters = dict(row.counters or {})
    counters["retry_not_before"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    row.counters = counters
    db.commit()
    assert background_job_runs.claim_ats_run(run_id, **claim_kwargs) is True
    db.expire_all()
    row = db.get(BackgroundJobRun, run_id)
    assert row.status == "running"
    assert int(row.counters["delivery_attempts"]) == 2


def test_dispatch_ack_cas_does_not_reopen_fast_worker_claim(db, monkeypatch):
    """A worker claim winning after the publisher's read stays ``running``."""

    from sqlalchemy import update
    from sqlalchemy.orm import Query

    org, _role = _seed(db)
    db.commit()
    run_id = background_job_runs.create_run(
        kind=JOB_KIND_WORKABLE_OP,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        counters={"op_type": OP_POST_NOTE, "producer": "original"},
        status="dispatching",
    )
    assert isinstance(run_id, int)

    original_update = Query.update
    injected = False

    def _worker_wins_before_ack(statement, values, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            statement.session.execute(
                update(BackgroundJobRun)
                .where(BackgroundJobRun.id == run_id)
                .values(
                    status="running",
                    counters={
                        "op_type": OP_POST_NOTE,
                        "delivery_attempts": 1,
                        "worker_won": True,
                    },
                )
            )
        return original_update(statement, values, **kwargs)

    monkeypatch.setattr(Query, "update", _worker_wins_before_ack)

    assert background_job_runs.mark_dispatched(run_id) is False
    db.expire_all()
    row = db.get(BackgroundJobRun, run_id)
    assert row.status == "running"
    assert row.counters["worker_won"] is True
    assert "last_dispatched_at" not in row.counters


def test_recovery_rechecks_stale_scan_under_locked_claim(db, monkeypatch):
    """A competing Beat claim between scan and lock prevents a second publish."""

    from sqlalchemy import update
    from sqlalchemy.orm import Query

    org, _role = _seed(db)
    run = BackgroundJobRun(
        kind=JOB_KIND_WORKABLE_OP,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        status="queued",
        counters={
            "op_type": OP_POST_NOTE,
            "recovery_payload": "not-read-after-competing-claim",
            "last_dispatched_at": "2000-01-01T00:00:00+00:00",
        },
        started_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    db.add(run)
    db.commit()
    run_id = int(run.id)

    original_with_for_update = Query.with_for_update
    injected = False

    def _other_beat_wins(statement, *args, **kwargs):
        nonlocal injected
        if kwargs.get("skip_locked") and not injected:
            injected = True
            statement.session.execute(
                update(BackgroundJobRun)
                .where(BackgroundJobRun.id == run_id)
                .values(
                    status="dispatching",
                    counters={
                        "op_type": OP_POST_NOTE,
                        "recovery_payload": "owned-by-other-beat",
                        "last_recovery_claimed_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    },
                )
            )
            statement.session.expire_all()
        return original_with_for_update(statement, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", _other_beat_wins)
    with patch.object(run_workable_op_task, "apply_async") as publish:
        result = recover_dispatching_workable_ops.run(
            limit=10,
            older_than_seconds=120,
            running_older_than_seconds=900,
        )

    assert result == {"scanned": 1, "recovered": 0, "failed": 0}
    publish.assert_not_called()
    db.expire_all()
    row = db.get(BackgroundJobRun, run_id)
    assert row.status == "dispatching"
    assert row.counters["recovery_payload"] == "owned-by-other-beat"


def test_retriable_ats_failure_releases_claim_before_celery_retry(db, monkeypatch):
    org, _role = _seed(db)
    db.commit()
    run_id = background_job_runs.create_run(
        kind=JOB_KIND_WORKABLE_OP,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(org.id),
        organization_id=int(org.id),
        counters={"op_type": OP_POST_NOTE},
        status="queued",
    )
    assert isinstance(run_id, int)
    monkeypatch.setattr(
        assessment_tasks,
        "_acquire_workable_org_mutex",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(assessment_tasks, "mark_workable_op_pending", lambda *_: None)
    provider_error = WorkableWritebackError(
        action="note",
        code="api_error",
        message="temporary",
        retriable=True,
    )
    with patch(
        "app.services.workable_op_runner.execute_op",
        side_effect=provider_error,
    ), patch.object(
        run_workable_op_task,
        "retry",
        side_effect=RuntimeError("retry scheduled"),
    ):
        with pytest.raises(RuntimeError, match="retry scheduled"):
            run_workable_op_task.run(
                job_run_id=run_id,
                organization_id=int(org.id),
                op_type=OP_POST_NOTE,
                payload={"application_id": 123, "user_id": 456, "body": "note"},
            )

    db.expire_all()
    row = db.get(BackgroundJobRun, run_id)
    assert row.status == "queued"
    assert row.counters.get("retry_not_before")


def test_dispatch_migration_downgrade_normalizes_leased_outreach_sends():
    from pathlib import Path

    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "174_async_dispatch_recovery.py"
    ).read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", maxsplit=1)[1]
    normalize_at = downgrade.index("SET status = 'queued'")
    sending_at = downgrade.index("WHERE status = 'sending'", normalize_at)
    drop_lease_at = downgrade.index(
        'op.drop_column("outreach_messages", column)',
        sending_at,
    )
    assert normalize_at < sending_at < drop_lease_at


def test_approve_batch_requeues_when_job_tracking_cannot_be_created(db):
    from app.actions import approve_decision as approve_decision_action
    from app.actions.types import ACTOR_RECRUITER, Actor

    org, role = _seed(db)
    decision = _add_processing_decision(
        db, org, role, status="pending", decision_type="reject"
    )
    db.commit()

    with patch("app.services.background_job_runs.create_run", return_value=None):
        with pytest.raises(AtsJobRunPersistenceError):
            approve_decision_action.enqueue_batch(
                db,
                Actor(type=ACTOR_RECRUITER, user_id=None),
                organization_id=int(org.id),
                decision_ids=[int(decision.id)],
                expected_decision_types={
                    str(int(decision.id)): str(decision.decision_type)
                },
            )

    db.expire_all()
    restored = db.get(AgentDecision, int(decision.id))
    assert restored.status == "pending"
    assert "No provider update was sent" in (restored.resolution_note or "")


def test_override_requeues_when_job_tracking_cannot_be_created(db):
    from app.actions import override_decision as override_decision_action
    from app.actions.types import ACTOR_RECRUITER, Actor

    org, role = _seed(db)
    decision = _add_processing_decision(
        db, org, role, status="pending", decision_type="reject"
    )
    db.commit()

    with patch("app.services.background_job_runs.create_run", return_value=None):
        with pytest.raises(AtsJobRunPersistenceError):
            override_decision_action.enqueue(
                db,
                Actor(type=ACTOR_RECRUITER, user_id=None),
                organization_id=int(org.id),
                decision_id=int(decision.id),
                override_action="advance",
                expected_decision_type=str(decision.decision_type),
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
