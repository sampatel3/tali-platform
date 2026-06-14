import logging

from .celery_app import celery_app
from ..components.integrations.workable.sync_runner import execute_workable_sync_run

logger = logging.getLogger(__name__)

# Bounded exponential backoff for transient Workable failures (429/5xx).
# 60s → 120s → … capped at 15min over 5 attempts.
_DISQUALIFY_MAX_RETRIES = 5
_DISQUALIFY_BACKOFF_CAP_SECONDS = 900


def _disqualify_retry_countdown(retries: int) -> int:
    return min(_DISQUALIFY_BACKOFF_CAP_SECONDS, 60 * (2 ** max(0, retries)))


# Retry budget for transient api-error (429/5xx) backoff on single ops.
_DISPATCH_MAX_RETRIES = 12

# Lock-wait has its OWN, much larger budget — a large approve batch holds the
# per-org mutex for its WHOLE duration (minutes), so a concurrently-submitted
# batch must wait that out rather than time out after ~70s and fail. ~60
# attempts × 5-15s jitter ≈ 10 min — comfortably longer than the ~2-min
# heartbeat TTL a holder leaks for on a worker kill, so a waiting batch
# reliably re-acquires once a leak self-clears, yet still bounded so it gives
# up if something is genuinely wedged. Re-enqueued as fresh tasks (not
# self.retry) so this never eats the api-error retry budget.
_LOCK_WAIT_MAX_ATTEMPTS = 60


def _lock_wait_countdown() -> int:
    """Jittered wait while the per-org mutex is held by another Workable write.
    NOT a rate-limit backoff. A held lock can persist for the length of a large
    batch, so we keep re-checking (see _LOCK_WAIT_MAX_ATTEMPTS). Jitter spreads
    the herd."""
    import random

    return random.randint(5, 15)


@celery_app.task(
    bind=True,
    name="app.tasks.workable_tasks.run_workable_op",
    max_retries=_DISPATCH_MAX_RETRIES,
    # Survive a worker killed mid-batch (deploy SIGKILL). ``acks_late`` keeps
    # the message un-acked until the task finishes, so a killed task is
    # re-delivered instead of silently lost; ``reject_on_worker_lost`` is what
    # actually re-queues it (default False drops acks_late tasks on worker
    # loss). Set per-task, NOT globally — a task that *crashes* the worker
    # (OOM/segfault) would otherwise loop forever. Re-delivery is safe: the
    # approve batch + every single op re-query each decision/application and
    # skip anything no longer in ``processing`` (idempotent).
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_workable_op_task(
    self,
    job_run_id: int | None,
    organization_id: int,
    op_type: str,
    payload: dict,
    lock_attempt: int = 0,
) -> dict:
    """Generic serialized runner shell for ALL Workable write-backs.

    Owns the cross-cutting concerns; the per-op work lives in
    ``app.services.workable_op_runner``:
    - Per-org mutex (shared with sync) so writes are strictly sequential — no
      rate-limit bursts. Lock contention retries fast; on exhaustion the op is
      surfaced and the job fails.
    - BackgroundJobRun bookkeeping (Settings → Background jobs).
    - Retry with backoff on a transient ``WorkableWritebackError`` (429/5xx);
      on a terminal failure the op surfaces (re-queues the decision / records a
      ``workable_*_failed`` event) so nothing silently drops.
    """
    from ..platform.database import SessionLocal
    from ..services import background_job_runs
    from ..services import workable_op_runner as runner
    from ..services.workable_actions_service import WorkableWritebackError
    from .assessment_tasks import (
        _acquire_workable_org_mutex,
        _release_workable_org_mutex,
        mark_workable_op_pending,
    )

    eager = bool(getattr(self.request, "is_eager", False))
    # Refresh the op-pending signal on every run — including each lock-wait
    # re-enqueue below — so the periodic syncs keep yielding the per-org mutex
    # for as long as this write is waiting. Self-expires once we stop retrying.
    mark_workable_op_pending(int(organization_id))
    # Short TTL + heartbeat (deploy-safe): if this worker is SIGKILLed
    # mid-write the heartbeat thread dies with it and the lock auto-expires in
    # ~2 min, instead of leaking for the 30-min static TTL and blocking ALL
    # Workable writes for this org until then.
    lock = _acquire_workable_org_mutex(
        int(organization_id), source=f"workable_op:{op_type}", heartbeat=True
    )
    if lock is None:
        # Held by another Workable write (often a large approve batch that holds
        # the lock for its whole run). Wait it out: re-enqueue a FRESH task with
        # an incremented lock_attempt — separate from (and far larger than) the
        # api-error retry budget — keeping the job 'queued' until the lock frees,
        # instead of failing after ~70s.
        if eager:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=0)
        elif lock_attempt < _LOCK_WAIT_MAX_ATTEMPTS:
            run_workable_op_task.apply_async(
                kwargs={
                    "job_run_id": job_run_id,
                    "organization_id": int(organization_id),
                    "op_type": op_type,
                    "payload": payload,
                    "lock_attempt": lock_attempt + 1,
                },
                countdown=_lock_wait_countdown(),
            )
            return {
                "status": "lock_wait_requeued",
                "op_type": op_type,
                "attempt": lock_attempt + 1,
            }
        # Couldn't get the lock within the (much larger) wait window — surface + fail.
        db = SessionLocal()
        try:
            err = WorkableWritebackError(
                action=op_type, code="lock_timeout", message="Workable was busy", retriable=True
            )
            runner.surface_op_failure(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload, error=err
            )
        finally:
            db.close()
        background_job_runs.update_run(
            job_run_id, status="failed", error="Workable lock timeout", finished=True
        )
        return {"status": "lock_timeout", "op_type": op_type}

    db = SessionLocal()
    try:
        background_job_runs.update_run(job_run_id, status="running")
        try:
            result = runner.execute_op(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload
            )
        except WorkableWritebackError as exc:
            db.rollback()
            if exc.retriable and self.request.retries < self.max_retries:
                raise self.retry(
                    countdown=0 if eager else _disqualify_retry_countdown(self.request.retries)
                )
            runner.surface_op_failure(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload, error=exc
            )
            background_job_runs.update_run(
                job_run_id,
                status="failed",
                counters={"op_type": op_type, "code": exc.code},
                error=exc.message,
                finished=True,
            )
            return {"status": "failed", "op_type": op_type, "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — never leave a decision stuck in 'processing'
            db.rollback()
            logger.exception("run_workable_op: unexpected error op_type=%s", op_type)
            err = WorkableWritebackError(
                action=op_type, code="unexpected", message=str(exc)[:200], retriable=False
            )
            runner.surface_op_failure(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload, error=err
            )
            background_job_runs.update_run(
                job_run_id,
                status="failed",
                counters={"op_type": op_type, "code": "unexpected"},
                error=str(exc)[:300],
                finished=True,
            )
            return {"status": "failed", "op_type": op_type, "code": "unexpected"}

        result = result if isinstance(result, dict) else {}
        status = "completed"
        if result.get("requeued") or result.get("failed"):
            status = "completed_with_errors"
        background_job_runs.update_run(
            job_run_id, status=status, counters={**result, "op_type": op_type}, finished=True
        )
        # Shell's status/op_type win over any per-handler "status" key.
        return {**result, "status": status, "op_type": op_type}
    finally:
        db.close()
        _release_workable_org_mutex(lock)


# Watchdog timeout for a stuck approve batch. The batch handler
# (``_op_approve_decisions``) catches per-decision and never raises, so its
# ``BackgroundJobRun`` is ``running`` ONLY while actively draining the loop —
# a few minutes for 100 decisions. A run still ``queued``/``running`` past this
# means a dead task: worker killed mid-batch (deploy SIGKILL, finally never ran)
# or the lock-wait re-enqueue chain dropped. 15 min clears the longest realistic
# legitimate batch AND exceeds the max lock-wait window (~60 × 5-15s) with margin.
_STUCK_DECISION_BATCH_TIMEOUT_MINUTES = 15


@celery_app.task(
    name="app.tasks.workable_tasks.expire_stuck_decision_batches",
    bind=True,
    max_retries=0,
)
def expire_stuck_decision_batches(self) -> dict:
    """Recover approve batches stranded by a worker death — in either state.

    Two failure modes leave a ``decision_batch`` run with its decisions stuck in
    ``processing`` and no live task left to finish them:
    - ``running``: a SIGKILL (deploy) skips ``run_workable_op_task``'s finally
      block mid-write, so the run stays ``running`` forever.
    - ``queued``: the task died inside the lock-wait re-enqueue loop (mutex held
      by a concurrent Workable write). Each wait re-enqueues a FRESH countdown
      task, so a worker restart that drops that in-flight message breaks the
      chain and the run stays ``queued`` forever — it never reaches ``running``.

    ``acks_late`` re-delivers the running case eventually (slow — ~1h Redis
    visibility-timeout) but never covers the queued case. This recovers both
    within one beat tick: stale ``queued``/``running`` ``decision_batch`` runs
    are marked ``failed`` and their still-``processing`` decisions returned to
    the Hub queue (from ``counters['decision_ids']``, persisted at enqueue for
    exactly this).

    Scoped to ``decision_batch`` only — single ``workable_op`` runs retry with
    backoff and can be legitimately ``running`` for >2h, so reaping them here
    would false-fail a healthy retry; their worker-death is covered by
    ``acks_late`` re-delivery instead. The leaked Redis mutex is handled
    separately by the op-path heartbeat/short-TTL, not here.

    The 15-min cutoff exceeds the max lock-wait window (~60 attempts × 5-15s),
    so a healthily-waiting ``queued`` batch isn't reaped before its own chain
    would self-fail. A late-acquiring task after a boundary race is harmless: the
    batch handler idempotently skips decisions no longer in ``processing``.

    No-op when nothing is stuck. Idempotent — re-running skips decisions
    already moved out of ``processing`` and runs already out of ``running``.
    """
    from datetime import datetime, timedelta, timezone

    from ..models.agent_decision import AgentDecision
    from ..models.background_job_run import JOB_KIND_DECISION_BATCH, BackgroundJobRun
    from ..platform.database import SessionLocal

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES
    )
    db = SessionLocal()
    failed_run_ids: list[int] = []
    requeued_ids: list[int] = []
    try:
        stuck = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_DECISION_BATCH,
                BackgroundJobRun.status.in_(("queued", "running")),
                BackgroundJobRun.finished_at.is_(None),
                BackgroundJobRun.started_at < cutoff,
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        for run in stuck:
            original_status = run.status
            decision_ids = [
                int(x) for x in ((run.counters or {}).get("decision_ids") or [])
            ]
            for decision_id in decision_ids:
                decision = (
                    db.query(AgentDecision)
                    .filter(
                        AgentDecision.id == decision_id,
                        AgentDecision.organization_id == run.organization_id,
                    )
                    .first()
                )
                # Idempotent: skip anything already resolved / requeued.
                if decision is None or decision.status != "processing":
                    continue
                decision.status = "pending"
                decision.resolution_note = (
                    f"Returned to queue by watchdog: approve batch (job {run.id}) "
                    f"stalled in '{original_status}' >{_STUCK_DECISION_BATCH_TIMEOUT_MINUTES}m "
                    "— worker killed mid-batch (deploy) or lost the lock-wait chain."
                )[:500]
                requeued_ids.append(decision_id)
            run.status = "failed"
            run.finished_at = now
            run.error = (
                run.error
                or f"watchdog: stuck in '{original_status}' >{_STUCK_DECISION_BATCH_TIMEOUT_MINUTES}m — worker killed mid-batch or lost the lock-wait chain"
            )
            failed_run_ids.append(int(run.id))
        if failed_run_ids:
            db.commit()
            logger.warning(
                "expire_stuck_decision_batches: failed %d run(s) %s, requeued %d decision(s) %s",
                len(failed_run_ids),
                failed_run_ids,
                len(requeued_ids),
                requeued_ids,
            )
    except Exception:
        db.rollback()
        logger.exception("expire_stuck_decision_batches failed")
        return {"status": "error"}
    finally:
        db.close()
    return {
        "status": "ok",
        "failed_run_count": len(failed_run_ids),
        "requeued_decision_count": len(requeued_ids),
        "job_run_ids": failed_run_ids,
        "decision_ids": requeued_ids,
    }


@celery_app.task(name="app.tasks.workable_tasks.run_workable_sync_run")
def run_workable_sync_run_task(
    org_id: int,
    run_id: int,
    mode: str = "metadata",
    selected_job_shortcodes: list[str] | None = None,
):
    logger.info(
        "Executing Workable sync task org_id=%s run_id=%s mode=%s selected_jobs=%s",
        org_id,
        run_id,
        mode,
        len(selected_job_shortcodes or []),
    )
    execute_workable_sync_run(
        org_id=org_id,
        run_id=run_id,
        mode=mode,
        selected_job_shortcodes=selected_job_shortcodes,
    )
    return {
        "status": "ok",
        "org_id": org_id,
        "run_id": run_id,
        "mode": mode,
        "selected_jobs_count": len(selected_job_shortcodes or []),
    }


@celery_app.task(
    bind=True,
    name="app.tasks.workable_tasks.retry_workable_disqualify",
    max_retries=_DISQUALIFY_MAX_RETRIES,
)
def retry_workable_disqualify_task(self, application_id: int, reason: str | None = None) -> dict:
    """Re-attempt a Workable disqualify that failed on the synchronous reject
    path (typically a transient 429).

    Without this, Tali's local outcome stays ``rejected`` while Workable still
    shows the candidate active — permanent drift with no reconciliation. Runs
    bounded, backed-off retries. Idempotent: skips if the candidate is no
    longer rejected in Tali (recruiter override) or has already been
    disqualified in Workable. On exhaustion, records the failure and stops —
    Taali never emails the candidate (job comms belong to the ATS).
    """
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from ..models.candidate_application import CandidateApplication
    from ..models.candidate_application_event import CandidateApplicationEvent
    from ..models.organization import Organization
    from ..platform.database import SessionLocal
    from ..services.workable_actions_service import disqualify_candidate_in_workable

    db = SessionLocal()
    try:
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if app is None:
            return {"status": "skipped", "reason": "not_found", "application_id": application_id}
        # Recruiter may have overridden the reject between attempts — don't
        # disqualify someone who's no longer rejected in Tali.
        if app.application_outcome != "rejected":
            return {"status": "skipped", "reason": "not_rejected", "application_id": application_id}
        # A prior attempt (or the original sync call) may have already landed.
        already = (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == application_id,
                CandidateApplicationEvent.event_type == "workable_disqualified",
            )
            .first()
        )
        if already is not None:
            return {"status": "skipped", "reason": "already_disqualified", "application_id": application_id}

        org = (
            db.query(Organization)
            .filter(Organization.id == app.organization_id)
            .first()
        )
        result = disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=app.role,
            reason=reason or "Rejected via Taali",
            withdrew=False,
        )
        if result.get("success"):
            config = result.get("config") or {}
            append_application_event(
                db,
                app=app,
                event_type="workable_disqualified",
                actor_type="system",
                reason=reason or result.get("message") or "Workable disqualified (retry)",
                metadata={
                    "action": result.get("action"),
                    "code": result.get("code"),
                    "workable_actor_member_id": config.get("actor_member_id"),
                    "workable_disqualify_reason_id": config.get("workable_disqualify_reason_id"),
                    "source": "retry_workable_disqualify",
                    "retries": self.request.retries,
                },
            )
            db.commit()
            return {"status": "ok", "application_id": application_id}

        # Retry only transient API errors; config/linkage failures won't fix
        # themselves and shouldn't burn retries.
        if result.get("code") == "api_error" and self.request.retries < self.max_retries:
            db.rollback()
            raise self.retry(countdown=_disqualify_retry_countdown(self.request.retries))

        # Give up: record the final failure for the audit trail. The local
        # reject already stands in Taali; the candidate is NOT emailed —
        # candidate job communication belongs to the ATS, not Taali.
        append_application_event(
            db,
            app=app,
            event_type="workable_writeback_failed",
            actor_type="system",
            reason=(result.get("message") or "Workable disqualify failed") + " (retry exhausted)",
            metadata={
                "code": result.get("code"),
                "source": "retry_workable_disqualify",
                "retries": self.request.retries,
            },
        )
        db.commit()
        return {"status": "failed", "application_id": application_id, "code": result.get("code")}
    finally:
        db.close()
