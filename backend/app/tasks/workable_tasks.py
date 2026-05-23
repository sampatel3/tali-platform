import logging

from .celery_app import celery_app
from ..components.integrations.workable.sync_runner import execute_workable_sync_run

logger = logging.getLogger(__name__)

# Bounded exponential backoff for the disqualify retry. Workable rate limits
# (429) clear within minutes; 60s → 120s → … capped at 15min over 5 attempts
# spans ~30min of best-effort retries before we give up and email the candidate.
_DISQUALIFY_MAX_RETRIES = 5
_DISQUALIFY_BACKOFF_CAP_SECONDS = 900


def _disqualify_retry_countdown(retries: int) -> int:
    return min(_DISQUALIFY_BACKOFF_CAP_SECONDS, 60 * (2 ** max(0, retries)))


# Decision types whose Workable write is a safely-replayable state change
# (disqualify / stage move). These are gated: a Workable failure aborts the
# local commit and re-queues the decision. send_assessment / resend_invite are
# NOT gated — they fire an invite *email* that can't be un-sent, so re-queuing
# would double-email; their Workable stage move stays best-effort.
_GATED_DECISION_TYPES = frozenset(
    {"reject", "skip_assessment_reject", "advance_to_interview"}
)
# Max times a batch task waits for the per-org Workable lock before giving up
# and returning the batch to the queue.
_DISPATCH_MAX_RETRIES = 12


def _lock_wait_countdown() -> int:
    """Short, jittered wait when the per-org mutex is simply held by another
    write. This is NOT a rate-limit backoff — the lock frees in seconds — so a
    100-decision batch drains quickly instead of stretching over the long 429
    backoff. Jitter spreads the herd so they don't all wake at once."""
    import random

    return random.randint(3, 9)


def _requeue_decision(decision_id: int, organization_id: int, *, note: str) -> None:
    """Flip a processing decision back to pending so it returns to the Hub
    queue. Opens its own short session; idempotent (only acts on processing)."""
    from ..models.agent_decision import AgentDecision
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id == decision_id,
                AgentDecision.organization_id == organization_id,
            )
            .first()
        )
        if decision is None or decision.status != "processing":
            return
        decision.status = "pending"
        decision.resolution_note = (note or "")[:500] or None
        db.commit()
    finally:
        db.close()


def _requeue_in_session(db, decision_id: int, organization_id: int, *, note: str) -> None:
    """Return a processing decision to the queue using the caller's session
    (after a rollback). Idempotent: only acts on a still-processing row."""
    from ..models.agent_decision import AgentDecision

    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None or decision.status != "processing":
        return
    decision.status = "pending"
    decision.resolution_note = (note or "")[:500] or None
    db.commit()


@celery_app.task(
    bind=True,
    name="app.tasks.workable_tasks.process_decision_batch",
    max_retries=_DISPATCH_MAX_RETRIES,
)
def process_decision_batch_task(
    self,
    job_run_id: int | None,
    organization_id: int,
    decision_ids: list[int],
    user_id: int | None = None,
    note: str | None = None,
    workable_target_stage: str | None = None,
) -> dict:
    """Drain a batch of approved decisions in the background, serialized per org.

    One row per recruiter request (single approve or a 100-row bulk approve).
    We hold the per-org Workable mutex (shared with sync) for the whole batch so
    the writebacks run strictly sequentially — no rate-limit bursts. Each
    decision's local Tali change is committed only after its Workable write
    succeeds (gated types: reject / advance); a decision whose writeback fails
    is returned to the queue (status → pending) and the batch keeps going.
    Progress is mirrored to the BackgroundJobRun for Settings → Background jobs.
    """
    from ..actions import approve_decision as approve_decision_action
    from ..actions.types import ACTOR_RECRUITER, Actor
    from ..models.agent_decision import AgentDecision
    from ..platform.database import SessionLocal
    from ..services import background_job_runs
    from ..services.workable_actions_service import (
        WorkableWritebackError,
        strict_workable_writes,
    )
    from .assessment_tasks import (
        _acquire_workable_org_mutex,
        _release_workable_org_mutex,
    )

    ids = [int(x) for x in (decision_ids or [])]

    # Serialize all Workable activity for this org. None == another task holds
    # the lock (sync or another batch); wait our turn. False == no Redis (tests
    # / Redis down) → run unguarded.
    lock = _acquire_workable_org_mutex(int(organization_id), source="decision_batch")
    if lock is None:
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=_lock_wait_countdown())
        # Couldn't get the lock in the window — return the whole batch to the queue.
        for did in ids:
            _requeue_decision(
                did, organization_id, note="Returned to queue: Workable was busy."
            )
        background_job_runs.update_run(
            job_run_id,
            status="failed",
            counters={"total": len(ids), "succeeded": 0, "requeued": len(ids), "failed": 0},
            error="Could not acquire the Workable lock within the retry window.",
            finished=True,
        )
        return {"status": "requeued_all", "reason": "lock_timeout", "count": len(ids)}

    counters = {"total": len(ids), "succeeded": 0, "requeued": 0, "failed": 0}
    background_job_runs.update_run(job_run_id, status="running", counters=counters)
    actor = Actor(type=ACTOR_RECRUITER, user_id=int(user_id) if user_id else None)

    db = SessionLocal()
    try:
        for idx, decision_id in enumerate(ids, start=1):
            decision = (
                db.query(AgentDecision)
                .filter(
                    AgentDecision.id == decision_id,
                    AgentDecision.organization_id == organization_id,
                )
                .first()
            )
            # Idempotent: only a still-processing row is ours to act on.
            if decision is None or decision.status != "processing":
                continue
            gated = decision.decision_type in _GATED_DECISION_TYPES
            try:
                if gated:
                    with strict_workable_writes():
                        approve_decision_action.run(
                            db,
                            actor,
                            organization_id=int(organization_id),
                            decision_id=int(decision_id),
                            note=note,
                            workable_target_stage=workable_target_stage,
                        )
                else:
                    approve_decision_action.run(
                        db,
                        actor,
                        organization_id=int(organization_id),
                        decision_id=int(decision_id),
                        note=note,
                        workable_target_stage=workable_target_stage,
                    )
                db.commit()
                counters["succeeded"] += 1
            except WorkableWritebackError as exc:
                # Nothing committed — roll back so we never leave a Tali outcome
                # applied without the Workable write, then return it to the queue.
                db.rollback()
                _requeue_in_session(
                    db,
                    decision_id,
                    organization_id,
                    note=f"Returned to queue: Workable writeback failed ({exc.code}). {exc.message}",
                )
                counters["requeued"] += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not halt the batch
                db.rollback()
                logger.exception(
                    "decision batch: unexpected error decision_id=%s", decision_id
                )
                _requeue_in_session(
                    db,
                    decision_id,
                    organization_id,
                    note=f"Returned to queue after an unexpected error: {str(exc)[:180]}",
                )
                counters["failed"] += 1
            # Flush progress periodically so Settings shows a moving bar.
            if idx % 10 == 0:
                background_job_runs.update_run(job_run_id, counters=counters)

        status = (
            "completed"
            if counters["requeued"] == 0 and counters["failed"] == 0
            else "completed_with_errors"
        )
        background_job_runs.update_run(
            job_run_id, status=status, counters=counters, finished=True
        )
        return {"status": status, **counters}
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
