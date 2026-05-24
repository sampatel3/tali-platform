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
# attempts × 5-15s jitter ≈ 10 min; still well under the 30-min mutex TTL so a
# genuinely leaked lock is given up on. Re-enqueued as fresh tasks (not
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
    )

    eager = bool(getattr(self.request, "is_eager", False))
    lock = _acquire_workable_org_mutex(int(organization_id), source=f"workable_op:{op_type}")
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
    disqualified in Workable. On exhaustion, sends the Taali rejection email
    so the candidate is still notified.
    """
    from ..actions.reject_application import _dispatch_rejection_email
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

        # Give up: record the final failure and notify the candidate directly
        # so a permanent Workable outage doesn't silently swallow the rejection.
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
        candidate = app.candidate
        candidate_email = (getattr(candidate, "email", "") or "").strip() if candidate else ""
        if candidate_email:
            position = (
                getattr(app.role, "name", None)
                or getattr(candidate, "position", None)
                or "the role you applied for"
            )
            _dispatch_rejection_email(
                candidate_email=candidate_email,
                candidate_name=(candidate.full_name or candidate.email),
                org_name=(org.name if org else "the hiring team"),
                position=position,
            )
        return {"status": "failed", "application_id": application_id, "code": result.get("code")}
    finally:
        db.close()
