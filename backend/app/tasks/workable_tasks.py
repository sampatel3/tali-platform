import logging

from ..services.ats_move_result_policy import terminalize_skipped_move_result
from ..services.ats_op_mutex_namespaces import op_mutex_namespaces as _op_mutex_namespaces
from .celery_app import celery_app
from .retry_safety import raise_secret_safe_task_retry as _retry_safely, raise_secret_safe_task_retry_code as _retry_code_safely
from .workable_sync_serialization import execute_serialized_workable_sync

logger = logging.getLogger(__name__)
_DISQUALIFY_MAX_RETRIES = 5
_DISQUALIFY_BACKOFF_CAP_SECONDS = 900


def _disqualify_retry_countdown(retries: int) -> int:
    return min(_DISQUALIFY_BACKOFF_CAP_SECONDS, 60 * (2 ** max(0, retries)))


_DISPATCH_MAX_RETRIES = 12

_LOCK_WAIT_MAX_ATTEMPTS = 60


def _lock_wait_countdown() -> int:
    """Jittered wait while the per-org mutex is held by another Workable write."""
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
    from ..models.background_job_run import (
        JOB_KIND_DECISION_BATCH,
        JOB_KIND_WORKABLE_OP,
    )
    from ..services import background_job_runs
    from ..services import workable_op_runner as runner
    from ..services.workable_actions_service import WorkableWritebackError
    from . import workable_op_lease as lease
    from .assessment_tasks import (
        _acquire_workable_org_mutex,
        _release_workable_org_mutex,
        _workable_mutex_ownership_lost,
        mark_workable_op_pending,
    )
    is_cv_gap = op_type == runner.OP_REJECT_CV_GAP
    if (
        isinstance(job_run_id, bool)
        or not isinstance(job_run_id, int)
        or job_run_id <= 0
    ):
        # Defense in depth: every production publisher must reserve a durable
        # BackgroundJobRun first. If a legacy/direct caller bypasses that gate,
        # surface the failure (including decision requeue / outcome receipt)
        # without touching the ATS provider.
        logger.error(
            "run_workable_op refused unmetered ATS op organization_id=%s op_type=%s",
            organization_id,
            op_type,
        )
        db = SessionLocal()
        try:
            error = WorkableWritebackError(
                action=op_type,
                code="job_run_persistence_failed",
                message="ATS operation had no durable background-job receipt",
                retriable=False,
            )
            runner.surface_op_failure(
                db,
                organization_id=int(organization_id),
                op_type=op_type,
                payload=payload,
                error=error,
            )
        except Exception:  # pragma: no cover - defensive surfacing only
            logger.exception("failed to surface unmetered ATS op_type=%s", op_type)
        finally:
            db.close()
        return {
            "status": "failed",
            "op_type": op_type,
            "code": "job_run_persistence_failed",
        }

    eager = bool(getattr(self.request, "is_eager", False))
    expected_kind = (
        JOB_KIND_DECISION_BATCH
        if op_type == runner.OP_APPROVE_DECISIONS
        else JOB_KIND_WORKABLE_OP
    )
    legacy_note = False
    if op_type == runner.OP_POST_NOTE:
        from ..services.ats_note_provider import AtsNoteProviderFailure
        from ..services.ats_note_rolling_compat import (
            claim_legacy_post_note_run,
            prepare_post_note_runtime_payload,
        )

        db = SessionLocal()
        try:
            payload = {**payload, "_job_run_id": int(job_run_id)}
            try:
                payload, legacy_note = prepare_post_note_runtime_payload(
                    db,
                    organization_id=int(organization_id),
                    payload=payload,
                )
            except AtsNoteProviderFailure as exc:
                db.rollback()
                failed_claim = background_job_runs.fail_claimable_ats_run_before_provider(
                    job_run_id,
                    organization_id=int(organization_id),
                    expected_kind=expected_kind,
                    op_type=op_type,
                    code=exc.code,
                    error=exc.message,
                )
                if not failed_claim:
                    return {
                        "status": "already_terminal",
                        "op_type": op_type,
                        "job_run_id": job_run_id,
                    }
                return {"status": "failed", "op_type": op_type, "code": exc.code}
            db.rollback()
        finally:
            db.close()
    # Refresh the op-pending signal on every run — including each lock-wait
    # re-enqueue below — so the periodic syncs keep yielding the per-org mutex
    # for as long as this write is waiting. Self-expires once we stop retrying.
    mark_workable_op_pending(int(organization_id))
    # Per-org mutex NAMESPACE: a Bullhorn-connected org takes the bullhorn lock
    # (build plan §6 "namespace bullhorn") so a Bullhorn write and a Bullhorn sync
    # for the same org never talk to the API concurrently; Workable orgs keep the
    # default (Workable) namespace. Same shared mutex util either way.
    mutex_namespaces = _op_mutex_namespaces(int(organization_id), payload)
    # Short TTL + heartbeat (deploy-safe): if this worker is SIGKILLed
    # mid-write the heartbeat thread dies with it and the lock auto-expires in
    # ~2 min, instead of leaking for the 30-min static TTL and blocking ALL
    # ATS writes for this org until then.
    locks = []
    lock_blocked = False
    for mutex_namespace in mutex_namespaces:
        lock = _acquire_workable_org_mutex(
            int(organization_id),
            source=f"ats_op:{op_type}",
            heartbeat=True,
            namespace=mutex_namespace,
        )
        # A held lock and an unavailable lock backend both defer the provider
        # write. Running any ATS operation unguarded can race a sync/write and
        # duplicate effects or exhaust provider rate/token state.
        if lock is None or lock is False:
            lock_blocked = True
            for held in reversed(locks):
                _release_workable_org_mutex(held)
            locks = []
            break
        locks.append(lock)
    if lock_blocked:
        # Held by another Workable write (often a large approve batch that holds
        # the lock for its whole run). Wait it out: re-enqueue a FRESH task with
        # an incremented lock_attempt — separate from (and far larger than) the
        # api-error retry budget — keeping the job 'queued' until the lock frees,
        # instead of failing after ~70s.
        if eager:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=0)
        elif lock_attempt < _LOCK_WAIT_MAX_ATTEMPTS:
            try:
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
            except Exception as exc:
                if op_type != runner.OP_OVERRIDE_DECISION:
                    raise
                reason = (
                    "Returned to queue: the ATS override lost its background "
                    "lock-wait delivery. Taali did not replay the ATS action; "
                    "review the decision and try again."
                )
                outcome = runner.compensate_override_delivery_loss(
                    organization_id=int(organization_id),
                    decision_id=int(payload["decision_id"]),
                    job_run_id=job_run_id,
                    reason=reason,
                    error_code="lock_wait_queue_unavailable",
                    allowed_run_statuses=("queued",),
                )
                logger.error(
                    "ATS override lock-wait kick failed; compensation status=%s "
                    "run_id=%s decision_id=%s error_type=%s",
                    outcome.get("status"),
                    job_run_id,
                    payload.get("decision_id"),
                    type(exc).__name__,
                )
                if outcome.get("status") not in {
                    "compensated",
                    "already_terminal_or_active",
                }:
                    raise
                return {
                    "status": "delivery_compensated",
                    "op_type": op_type,
                    "decision_id": int(payload["decision_id"]),
                    "requeued": bool(outcome.get("requeued")),
                }
            return {
                "status": "lock_wait_requeued",
                "op_type": op_type,
                "attempt": lock_attempt + 1,
            }
        # Couldn't get the lock within the (much larger) wait window — surface + fail.
        db = SessionLocal()
        try:
            err = WorkableWritebackError(
                action=op_type, code="lock_timeout", message="ATS was busy", retriable=True
            )
            runner.surface_op_failure(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload, error=err
            )
        finally:
            db.close()
        background_job_runs.update_run(
            job_run_id, status="failed", error="ATS lock timeout", finished=True, pre_provider_failure=True
        )
        return {"status": "lock_timeout", "op_type": op_type}

    db = SessionLocal()
    try:
        legacy_claim = (
            claim_legacy_post_note_run(
                run_id=int(job_run_id),
                organization_id=int(organization_id),
                payload=payload,
            )
            if legacy_note
            else None
        )
        if legacy_claim == "persistence_failed":
            if self.request.retries < self.max_retries:
                _retry_code_safely(
                    self,
                    "workable_operation:legacy_recovery_persistence_failed",
                    countdown=(0 if eager else _disqualify_retry_countdown(self.request.retries)),
                )
            failed_claim = background_job_runs.fail_claimable_ats_run_before_provider(
                job_run_id,
                organization_id=int(organization_id),
                expected_kind=expected_kind,
                op_type=op_type,
                code="legacy_recovery_persistence_failed",
                error="Legacy ATS note recovery authority could not be persisted",
            )
            if not failed_claim:
                return {
                    "status": "already_terminal",
                    "op_type": op_type,
                    "job_run_id": job_run_id,
                }
            return {
                "status": "failed",
                "op_type": op_type,
                "code": "legacy_recovery_persistence_failed",
            }
        claimed = (
            legacy_claim == "claimed"
            if legacy_note
            else background_job_runs.claim_ats_run(
                job_run_id,
                organization_id=int(organization_id),
                expected_kind=expected_kind,
                op_type=op_type,
            )
        )
        if not claimed:
            return {
                "status": "already_terminal",
                "op_type": op_type,
                "job_run_id": job_run_id,
            }
        if lease.ownership_is_lost(locks, _workable_mutex_ownership_lost):
            # No provider call has happened in this attempt. Put the durable
            # claim back before retrying so an uncertain/expired lease cannot
            # allow this worker to overlap a replacement owner.
            countdown = 0 if eager else _lock_wait_countdown()
            background_job_runs.release_ats_run_for_retry(
                job_run_id,
                delay_seconds=countdown,
            )
            if self.request.retries < self.max_retries:
                _retry_code_safely(self, "workable_operation:mutex_lease_lost", countdown=countdown)
            return {
                "status": "mutex_lease_lost_deferred",
                "op_type": op_type,
                "job_run_id": job_run_id,
            }
        try:
            payload = (
                {**payload, "_job_run_id": int(job_run_id)}
                if is_cv_gap
                or op_type
                in {
                    runner.OP_MOVE_STAGE,
                    runner.OP_APPROVE_DECISIONS,
                    runner.OP_OVERRIDE_DECISION,
                    runner.OP_POST_NOTE,
                }
                else payload
            )
            result = lease.execute_with_lease_observer(
                runner=runner, db=db, organization_id=int(organization_id),
                op_type=op_type, payload=payload, locks=locks,
                ownership_lost=_workable_mutex_ownership_lost,
            )
            if lease.ownership_is_lost(locks, _workable_mutex_ownership_lost):
                # The provider result is authoritative and may already contain
                # a durable side-effect receipt. Never replay it merely because
                # lease ownership became uncertain during the external call.
                logger.warning(
                    "ATS mutex lease became uncertain during provider op "
                    "organization_id=%s op_type=%s",
                    organization_id,
                    op_type,
                )
        except WorkableWritebackError as exc:
            db.rollback()
            if (
                exc.retriable
                and lease.retry_is_proven_safe(
                    exc, locks, _workable_mutex_ownership_lost
                )
                and self.request.retries < self.max_retries
            ):
                countdown = (
                    0
                    if eager
                    else _disqualify_retry_countdown(self.request.retries)
                )
                # The DB claim, not Redis, is the duplicate-side-effect guard.
                # Explicitly release this attempt before publishing a legitimate
                # retry; an ambiguous duplicate delivery still sees ``running``
                # and is refused. The not-before receipt also keeps Beat from
                # defeating provider backoff if Celery loses the retry message.
                background_job_runs.release_ats_run_for_retry(
                    job_run_id,
                    delay_seconds=countdown,
                )
                _retry_safely(self, exc, operation="workable_operation", countdown=countdown)
            runner.surface_op_failure(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload, error=exc
            )
            background_job_runs.update_run(
                job_run_id,
                status="failed",
                counters=None if is_cv_gap else {"op_type": op_type, "code": exc.code},
                error=exc.message,
                finished=True,
            )
            return {"status": "failed", "op_type": op_type, "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — never leave a decision stuck in 'processing'
            db.rollback()
            error_type = type(exc).__name__
            logger.error(
                "run_workable_op: unexpected error op_type=%s error_type=%s",
                op_type,
                error_type,
            )
            err = WorkableWritebackError(
                action=op_type,
                code="unexpected",
                message=f"Unexpected ATS operation failure ({error_type})",
                retriable=False,
            )
            runner.surface_op_failure(
                db, organization_id=int(organization_id), op_type=op_type, payload=payload, error=err
            )
            background_job_runs.update_run(
                job_run_id,
                status="failed",
                counters=None if is_cv_gap else {
                    "op_type": op_type,
                    "code": "unexpected",
                    "error_type": error_type,
                },
                error=f"Unexpected ATS operation failure ({error_type})",
                finished=True,
            )
            return {"status": "failed", "op_type": op_type, "code": "unexpected"}

        result = result if isinstance(result, dict) else {}
        if failed_move := terminalize_skipped_move_result(db, int(organization_id), op_type, payload, result, job_run_id):
            return failed_move
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
        for lock in reversed(locks):
            _release_workable_org_mutex(lock)


@celery_app.task(name="app.tasks.workable_tasks.recover_dispatching_workable_ops")
def recover_dispatching_workable_ops(
    limit: int = 200,
    older_than_seconds: int = 120,
    running_older_than_seconds: int = 900,
) -> dict:
    """Replay stale durable status ops across every nonterminal delivery state.

    ``dispatching`` covers a broker exception before acceptance, ``queued`` an
    accepted message that never began, and ``running`` a worker death or failed
    final bookkeeping. Only status writes and receipt-keyed recruiter notes
    carry the encrypted recovery payload and are eligible.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from ..models.background_job_run import BackgroundJobRun, JOB_KIND_WORKABLE_OP
    from ..platform.config import settings
    from ..platform.database import SessionLocal
    from ..platform.secrets import decrypt_text
    from ..services import background_job_runs
    from ..services.background_job_runs import ATS_MAX_DELIVERY_ATTEMPTS
    from ..services.ats_note_rolling_compat import is_unrecoverable_legacy_note

    now = datetime.now(timezone.utc)
    queued_cutoff = now - timedelta(
        seconds=max(0, int(older_than_seconds))
    )
    running_cutoff = now - timedelta(
        seconds=max(0, int(running_older_than_seconds))
    )

    def _stamp(value) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _is_due(row: BackgroundJobRun) -> bool:
        counters = row.counters if isinstance(row.counters, dict) else {}
        retry_not_before = _stamp(counters.get("retry_not_before"))
        if retry_not_before is not None and retry_not_before > now:
            return False
        if row.status == "running":
            reference = _stamp(counters.get("last_started_at")) or _stamp(
                row.started_at
            )
            return reference is None or reference <= running_cutoff
        references = [
            _stamp(counters.get("last_recovery_claimed_at")),
            _stamp(counters.get("last_dispatched_at")),
            _stamp(counters.get("last_retry_scheduled_at")),
            retry_not_before,
            _stamp(row.started_at),
        ]
        reference = max((value for value in references if value is not None), default=None)
        return reference is None or reference <= queued_cutoff

    db = SessionLocal()
    recovered = 0
    failed = 0
    legacy_broker_only = 0
    try:
        rows = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
                BackgroundJobRun.status.in_(("dispatching", "queued", "running")),
                BackgroundJobRun.finished_at.is_(None),
            )
            .order_by(BackgroundJobRun.id.asc())
            .limit(max(1, int(limit)) * 4)
            .all()
        )
        due_row_ids = [
            int(row.id) for row in rows if _is_due(row)
        ][: max(1, int(limit))]
        # End the candidate-scan transaction. Each candidate is claimed below
        # under its own row lock, with the due decision repeated against fresh
        # state; two Beat pods may scan the same stale row, but only one can
        # publish it.
        db.rollback()
        for run_id in due_row_ids:
            row = (
                db.query(BackgroundJobRun)
                .filter(
                    BackgroundJobRun.id == run_id,
                    BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
                    BackgroundJobRun.status.in_(
                        ("dispatching", "queued", "running")
                    ),
                    BackgroundJobRun.finished_at.is_(None),
                )
                .populate_existing()
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if row is None or not _is_due(row):
                db.commit()
                continue
            counters = row.counters if isinstance(row.counters, dict) else {}
            encrypted_payload = str(counters.get("recovery_payload") or "")
            op_type = str(counters.get("op_type") or "")
            if int(counters.get("delivery_attempts") or 0) >= ATS_MAX_DELIVERY_ATTEMPTS:
                row.status = "failed"
                row.error = "ATS delivery attempt limit exhausted"
                row.finished_at = now
                row.counters = {
                    **counters,
                    "code": "delivery_attempts_exhausted",
                    "provider_called": False,
                }
                db.commit()
                failed += 1
                continue
            if is_unrecoverable_legacy_note(counters):
                # Old producers did not retain the note body. Its accepted
                # broker message remains the only safe delivery authority;
                # do not misclassify the visible queued run as corrupt.
                db.commit()
                legacy_broker_only += 1
                continue
            try:
                payload = json.loads(
                    decrypt_text(encrypted_payload, settings.SECRET_KEY)
                )
                if not isinstance(payload, dict) or not op_type:
                    raise ValueError("recovery payload is invalid")
            except Exception as exc:
                # Corrupt/undecryptable internal state cannot become valid on a
                # later Beat tick. Fail visibly instead of looping forever.
                error_type = type(exc).__name__
                row.status = "failed"
                row.error = f"ATS recovery payload invalid ({error_type})"
                row.finished_at = now
                row.counters = {
                    "op_type": op_type or "unknown",
                    "code": "recovery_payload_invalid",
                    "error_type": error_type,
                }
                db.commit()
                failed += 1
                logger.error(
                    "invalid ATS op recovery payload run_id=%s error_type=%s",
                    row.id,
                    error_type,
                )
                continue

            try:
                # Durable claim before broker publication. The row remains
                # locked through this commit, so a second Beat pod either skips
                # it or rechecks the fresh lease after this transaction wins.
                claimed_counters = dict(counters)
                claimed_counters["last_recovery_claimed_at"] = now.isoformat()
                row.counters = claimed_counters
                row.status = "dispatching"
                organization_id = int(row.organization_id)
                db.commit()
                run_workable_op_task.apply_async(
                    kwargs={
                        "job_run_id": run_id,
                        "organization_id": organization_id,
                        "op_type": op_type,
                        "payload": payload,
                    }
                )
                background_job_runs.mark_dispatched(run_id)
                recovered += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                logger.error(
                    "failed to recover ATS op run_id=%s error_type=%s",
                    run_id,
                    type(exc).__name__,
                )
        result = {
            "scanned": len(due_row_ids),
            "recovered": recovered,
            "failed": failed,
        }
        if legacy_broker_only:
            result["legacy_broker_only"] = legacy_broker_only
        return result
    finally:
        db.close()


# Watchdog timeout for a stuck approve batch. The batch handler
# (``_op_approve_decisions``) catches per-decision and never raises, so its
# ``BackgroundJobRun`` is ``running`` ONLY while actively draining the loop —
# a few minutes for 100 decisions. A run still ``queued``/``running`` past this
# means a dead task: worker killed mid-batch (deploy SIGKILL, finally never ran)
# or the lock-wait re-enqueue chain dropped. 15 min clears the longest realistic
# legitimate batch AND exceeds the max lock-wait window (~60 × 5-15s) with margin.
_STUCK_DECISION_BATCH_TIMEOUT_MINUTES = 15
# An override can legitimately sit between provider retries for up to 15
# minutes, and lock-wait chains last about 10 minutes. Give both ample margin;
# anything older has lost its only safe delivery and must return to HITL rather
# than replaying a possibly non-idempotent side effect.
_STUCK_OVERRIDE_TIMEOUT_MINUTES = 30


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

    Scoped to ``decision_batch`` only. Replay-safe single ``workable_op`` rows
    have their own encrypted-payload recovery sweep above; non-replayable
    decision batches instead need their decisions returned to the Hub. The
    leaked Redis mutex is handled separately by the op heartbeat/short TTL.

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
    from ..services.ats_job_run_timing import ats_attempt_started_at

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STUCK_DECISION_BATCH_TIMEOUT_MINUTES)
    db = SessionLocal()
    failed_run_ids, requeued_ids = [], []
    try:
        stuck = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_DECISION_BATCH,
                BackgroundJobRun.status.in_(("queued", "running")),
                BackgroundJobRun.finished_at.is_(None),
                BackgroundJobRun.started_at < cutoff,
            )
            .populate_existing().with_for_update(skip_locked=True)
            .all()
        )
        now = datetime.now(timezone.utc)
        for run in stuck:
            reference = ats_attempt_started_at(run)
            if reference is not None and reference >= cutoff:
                continue
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


@celery_app.task(
    name="app.tasks.workable_tasks.expire_stuck_override_ops",
    max_retries=0,
)
def expire_stuck_override_ops(
    limit: int = 200,
    timeout_minutes: int = _STUCK_OVERRIDE_TIMEOUT_MINUTES,
) -> dict:
    """Compensate stale non-replayable override deliveries without replaying.

    A queued message can be lost after broker acceptance, and a killed worker can
    leave a run in ``running`` after the task's acknowledgement rail disappears.
    The override payload is intentionally not persisted because it may include
    non-idempotent email/action semantics.  This watchdog therefore uses only the
    safe ``decision_id`` receipt: stale runs fail and a still-processing decision
    returns to the Hub for an explicit recruiter retry.
    """
    from datetime import datetime, timedelta, timezone

    from ..models.background_job_run import JOB_KIND_WORKABLE_OP, BackgroundJobRun
    from ..platform.database import SessionLocal
    from ..services import workable_op_runner as runner

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=max(1, int(timeout_minutes))
    )
    db = SessionLocal()
    try:
        rows = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
                BackgroundJobRun.status.in_(("queued", "running")),
                BackgroundJobRun.finished_at.is_(None),
                BackgroundJobRun.started_at <= cutoff,
            )
            .order_by(BackgroundJobRun.id.asc())
            .limit(max(1, min(int(limit), 1000)) * 4)
            .all()
        )
        candidates: list[tuple[int, int, int]] = []
        for row in rows:
            counters = row.counters if isinstance(row.counters, dict) else {}
            if str(counters.get("op_type") or "") != runner.OP_OVERRIDE_DECISION:
                continue
            try:
                decision_id = int(counters["decision_id"])
            except (KeyError, TypeError, ValueError):
                logger.error(
                    "stale ATS override lacks decision receipt run_id=%s",
                    row.id,
                )
                continue
            candidates.append(
                (int(row.id), int(row.organization_id), decision_id)
            )
            if len(candidates) >= max(1, min(int(limit), 1000)):
                break
    except Exception:
        db.rollback()
        logger.exception("expire_stuck_override_ops scan failed")
        return {"status": "error", "scanned": 0}
    finally:
        db.close()

    failed_run_ids: list[int] = []
    requeued_ids: list[int] = []
    skipped = 0
    errors = 0
    for run_id, organization_id, decision_id in candidates:
        reason = (
            f"Returned to queue by watchdog: ATS override job {run_id} lost its "
            f"delivery for more than {max(1, int(timeout_minutes))} minutes. "
            "Taali did not replay the ATS side effect; confirm the ATS state "
            "before trying again."
        )
        try:
            outcome = runner.compensate_override_delivery_loss(
                organization_id=organization_id,
                decision_id=decision_id,
                job_run_id=run_id,
                reason=reason,
                error_code="stale_delivery",
                allowed_run_statuses=("queued", "running"),
                stale_before=cutoff,
            )
        except Exception:
            errors += 1
            logger.exception(
                "expire_stuck_override_ops compensation failed run_id=%s",
                run_id,
            )
            continue
        if outcome.get("status") != "compensated":
            skipped += 1
            continue
        failed_run_ids.append(run_id)
        if outcome.get("requeued"):
            requeued_ids.append(decision_id)

    if failed_run_ids:
        logger.warning(
            "expire_stuck_override_ops: failed %d run(s) %s, requeued %d decision(s) %s",
            len(failed_run_ids),
            failed_run_ids,
            len(requeued_ids),
            requeued_ids,
        )
    return {
        "status": "ok" if not errors else "partial",
        "scanned": len(candidates),
        "failed_run_count": len(failed_run_ids),
        "requeued_decision_count": len(requeued_ids),
        "skipped": skipped,
        "errors": errors,
        "job_run_ids": failed_run_ids,
        "decision_ids": requeued_ids,
    }


@celery_app.task(
    bind=True,
    name="app.tasks.workable_tasks.run_workable_sync_run",
    max_retries=None,
)
def run_workable_sync_run_task(
    self,
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
    execute_serialized_workable_sync(
        self,
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
    """Retained legacy task: surface ambiguity without replaying provider I/O.

    Historical messages carry only ``application_id`` and ``reason``. They do
    not bind an operation id, app version, decision, provider target, or proof
    the original POST was not applied. The registration remains so queued old
    messages are consumed safely, but they now require reconciliation instead
    of blindly issuing another candidate-facing Workable request.
    """
    from ..models.candidate_application import CandidateApplication
    from ..models.candidate_application_event import CandidateApplicationEvent
    from ..platform.database import SessionLocal
    from ..services.legacy_workable_disqualify import (
        surface_ambiguous_legacy_disqualify,
    )

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

        receipt = surface_ambiguous_legacy_disqualify(
            db,
            app=app,
            reason=reason,
            source="retry_workable_disqualify_legacy_message",
        )
        db.commit()
        return {
            "status": "reconciliation_required",
            "application_id": application_id,
            "operation_id": receipt.get("operation_id"),
        }
    finally:
        db.close()
