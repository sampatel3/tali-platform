"""Generic serialized runner for ALL Workable write-backs.

Every recruiter/system action that writes to Workable routes through here. The
uniform goals are:

- **Serialized per org** — one provider conversation per org at a time.
- **Background + tracked** — every request has a visible ``BackgroundJobRun``.
- **Retried + never dropped** — transient failures retry; exhausted work surfaces.

This module holds handlers; the Celery shell owns coordination in
``app.tasks.workable_tasks.run_workable_op_task``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from . import ats_note_dispatch_identity
from .cv_gap_rejection_batch import run_cv_gap_rejection_batch
from .ats_operation_guards import recruiter_actor as _recruiter_actor
from .ats_operation_labels import active_ats_label as _active_ats_label
from .workable_actions_service import WorkableWritebackError
from .ats_job_run_errors import AtsJobRunPersistenceError
from .decision_requeue import requeue_processing_decision as _requeue_decision

logger = logging.getLogger("taali.workable_op_runner")


# Op type constants — also the dispatch keys.
OP_APPROVE_DECISIONS = "approve_decisions"
OP_OVERRIDE_DECISION = "override_decision"
OP_MOVE_STAGE = "move_stage"
OP_MANUAL_OUTCOME = "manual_outcome"
OP_POST_NOTE = "post_note"
OP_AUTO_REJECT = "auto_reject"
OP_REJECT_CV_GAP = "reject_cv_gap"
# Override actions whose Workable write is a safely-replayable state change
# (disqualify / stage move) — gated so a failure re-queues. send_assessment /
# hold are NOT gated (email side-effect / no-op).
_GATED_OVERRIDE_ACTIONS = frozenset({"reject", "advance", "skip_assessment_advance"})


# ---------------------------------------------------------------------------
# Op handlers. Each takes (db, organization_id, payload) and returns a result
# dict. Single-op handlers may raise WorkableWritebackError (the Celery shell
# turns a retriable one into a retry, and a terminal one into
# ``surface_op_failure``). The batch handler is self-contained: it commits per
# decision and never raises, so one bad row can't fail the whole batch.
# ---------------------------------------------------------------------------


def compensate_override_delivery_loss(
    *,
    organization_id: int,
    decision_id: int,
    job_run_id: int | None,
    reason: str,
    error_code: str,
    allowed_run_statuses: tuple[str, ...] = ("queued",),
    stale_before: datetime | None = None,
) -> dict:
    """Fail a non-replayable override delivery and return its decision to HITL.

    Override operations can contain email or other non-idempotent side effects,
    so a lost broker delivery must never be replayed from a stored payload.  The
    ``BackgroundJobRun`` is instead the coordination row: lock it, prove it is
    still in an eligible non-terminal state, fail it, and requeue only a decision
    that is still ``processing``.  A worker that already terminalized the run is
    left untouched; a worker that won the ``queued -> running`` race is likewise
    left alone by the immediate (queued-only) compensator.

    ``stale_before`` is used by the Beat watchdog.  For a running retry chain its
    latest ``last_started_at`` receipt is authoritative, so a healthy delayed
    retry is not reaped merely because the run row itself is old.
    """
    from ..models.background_job_run import JOB_KIND_WORKABLE_OP, BackgroundJobRun
    from ..platform.database import SessionLocal

    def _aware(value: object) -> datetime | None:
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

    db = SessionLocal()
    try:
        run = None
        if job_run_id is not None:
            run = (
                db.query(BackgroundJobRun)
                .filter(
                    BackgroundJobRun.id == int(job_run_id),
                    BackgroundJobRun.organization_id == int(organization_id),
                    BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
                )
                .with_for_update()
                .one_or_none()
            )
            if run is not None:
                counters = run.counters if isinstance(run.counters, dict) else {}
                if str(counters.get("op_type") or "") != OP_OVERRIDE_DECISION:
                    return {
                        "status": "wrong_op_type",
                        "job_run_id": int(run.id),
                        "requeued": False,
                    }
                if run.finished_at is not None or run.status not in allowed_run_statuses:
                    return {
                        "status": "already_terminal_or_active",
                        "job_run_id": int(run.id),
                        "run_status": run.status,
                        "requeued": False,
                    }
                if stale_before is not None:
                    reference = _aware(run.started_at)
                    if run.status == "running":
                        reference = _aware(counters.get("last_started_at")) or reference
                    if reference is not None and reference > stale_before:
                        return {
                            "status": "not_stale",
                            "job_run_id": int(run.id),
                            "run_status": run.status,
                            "requeued": False,
                        }

        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id == int(decision_id),
                AgentDecision.organization_id == int(organization_id),
            )
            .with_for_update()
            .one_or_none()
        )
        requeued = bool(decision is not None and decision.status == "processing")
        if requeued:
            decision.status = "pending"
            decision.resolution_note = (reason or "")[:500] or None

        now = datetime.now(timezone.utc)
        if run is not None:
            counters = dict(run.counters or {})
            counters.update(
                {
                    "op_type": OP_OVERRIDE_DECISION,
                    "decision_id": int(decision_id),
                    "failure_code": str(error_code or "delivery_lost")[:100],
                }
            )
            run.counters = counters
            run.status = "failed"
            run.finished_at = now
            run.error = str(reason or "ATS override delivery was lost")[:2000]

        db.commit()
        return {
            "status": "compensated",
            "job_run_id": int(run.id) if run is not None else None,
            "decision_id": int(decision_id),
            "requeued": requeued,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _op_approve_decisions(db: Session, organization_id: int, payload: dict) -> dict:
    from .workable_decision_approval import run_approval_batch

    return run_approval_batch(db, organization_id, payload)

def _op_override_decision(db: Session, organization_id: int, payload: dict) -> dict:
    """Apply one override with provider I/O outside every DB transaction."""
    from ..actions import override_decision as override_decision_action

    decision_id = int(payload["decision_id"])
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None:
        return {"status": "skipped", "reason": "not_processing", "decision_id": decision_id}

    actor = _recruiter_actor(payload.get("user_id"))
    override_action = payload.get("override_action")
    gated = override_action in _GATED_OVERRIDE_ACTIONS

    if gated:
        from .decision_provider_lifecycle import execute_decision_provider_lifecycle
        from .decision_provider_status import (
            decision_provider_confirmed_note_replay,
            decision_provider_needs_reconciliation,
        )

        if decision.status != "processing" and not (
            decision_provider_confirmed_note_replay(
                db,
                decision_id=decision_id,
                organization_id=organization_id,
            )
        ):
            return {
                "status": "skipped",
                "reason": "not_processing",
                "decision_id": decision_id,
            }
        try:
            return execute_decision_provider_lifecycle(
                db,
                organization_id=int(organization_id),
                decision_id=decision_id,
                disposition="overridden",
                actor=actor,
                override_action=override_action,
                note=payload.get("note"),
                target_stage=payload.get("workable_target_stage"),
                expected_decision_type=payload.get("expected_decision_type"),
                expected_role_family=payload.get("expected_role_family"),
                job_run_id=payload.get("_job_run_id"),
            )
        except WorkableWritebackError:
            db.rollback()
            if decision_provider_needs_reconciliation(
                db,
                decision_id=decision_id,
                organization_id=organization_id,
            ):
                return {
                    "status": "reconciliation_required",
                    "decision_id": decision_id,
                    "failed": True,
                }
            raise

    if decision.status != "processing":
        return {
            "status": "skipped",
            "reason": "not_processing",
            "decision_id": decision_id,
        }

    def _run():
        override_decision_action.run(
            db,
            actor,
            organization_id=int(organization_id),
            decision_id=decision_id,
            override_action=override_action,
            note=payload.get("note"),
            workable_target_stage=payload.get("workable_target_stage"),
            expected_decision_type=payload.get("expected_decision_type"),
            expected_role_family=payload.get("expected_role_family"),
        )

    _run()
    db.commit()
    return {"status": "ok", "decision_id": decision_id}


def _op_move_stage(db: Session, organization_id: int, payload: dict) -> dict:
    """Run the durable claim/provider/finalize stage-move lifecycle."""

    from .ats_stage_move_lifecycle import execute_stage_move_lifecycle

    observer = payload.get("_should_yield")
    return execute_stage_move_lifecycle(db, organization_id=int(organization_id), payload=payload, **({"should_yield": observer} if observer is not None else {}))


def _op_manual_outcome(db: Session, organization_id: int, payload: dict) -> dict:
    """Mirror a recruiter's manual outcome change to Workable (disqualify on
    reject, revert on re-open). The local outcome already committed in the
    route — this is the (retried) Workable writeback only."""
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from .ats_outcome_provider import (
        perform_outcome_provider_call,
        prepare_manual_outcome_provider_plan,
        stamp_bullhorn_outcome_success,
    )
    from .manual_outcome_lifecycle import (
        finalize_manual_outcome_success,
        preflight_manual_outcome,
    )

    superseded = preflight_manual_outcome(db, organization_id, payload)
    if superseded:
        return superseded
    plan, application_id = prepare_manual_outcome_provider_plan(
        db, organization_id=organization_id, payload=payload
    )
    target_outcome = payload.get("target_outcome")
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    provider = str(payload.get("provider") or "").strip().lower()
    db.rollback()
    assert not db.in_transaction()
    provider_result = perform_outcome_provider_call(plan)
    app = db.get(CandidateApplication, application_id)
    if app is None:
        return {
            "status": "manual_reconciliation_required",
            "application_id": application_id,
            "reason": "ATS confirmed the outcome but the local application is unavailable",
        }
    reconciliation = finalize_manual_outcome_success(
        db,
        app,
        payload,
        provider=provider,
        remote_status=provider_result.get("provider_remote_stage"),
        on_exact_success=lambda exact_app: stamp_bullhorn_outcome_success(
            exact_app, plan, provider_result
        ),
    )
    if reconciliation is not None:
        return reconciliation
    event_type = (
        f"{provider}_reverted" if target_outcome == "open" else f"{provider}_disqualified"
    )
    if provider == "bullhorn" and target_outcome != "open":
        event_type = "bullhorn_rejected"
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type="recruiter",
        actor_id=user_id,
        reason=reason or f"{provider.title()} outcome synced",
        metadata={
            "ats_provider": provider,
            "provider_target_id": payload.get("provider_target_id"),
            "target_outcome": target_outcome,
            "provider_remote_stage": provider_result.get("provider_remote_stage"),
        },
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def _op_post_note(db: Session, organization_id: int, payload: dict) -> dict:
    """Run every note shape through the canonical receipt lifecycle."""
    from .ats_note_provider import AtsNoteProviderFailure
    from .ats_note_rolling_compat import prepare_post_note_runtime_payload
    from .ats_note_runtime import execute_ats_note

    try:
        payload, _is_legacy = prepare_post_note_runtime_payload(
            db,
            organization_id=int(organization_id),
            payload=payload,
        )
    except AtsNoteProviderFailure as exc:
        db.rollback()
        return {
            "status": "failed",
            "application_id": 0,
            "failed": 1,
            "provider_called": False,
            "retriable": exc.retriable,
            "code": exc.code,
        }
    return execute_ats_note(
        db,
        organization_id=int(organization_id),
        payload=payload,
    )


def _op_auto_reject(db: Session, organization_id: int, payload: dict) -> dict:
    from .auto_reject_op import execute_auto_reject_op

    return execute_auto_reject_op(db, organization_id, payload)


_HANDLERS: dict[str, Callable[[Session, int, dict], dict]] = {
    OP_APPROVE_DECISIONS: _op_approve_decisions,
    OP_OVERRIDE_DECISION: _op_override_decision,
    OP_MOVE_STAGE: _op_move_stage,
    OP_MANUAL_OUTCOME: _op_manual_outcome,
    OP_POST_NOTE: _op_post_note,
    OP_AUTO_REJECT: _op_auto_reject,
    OP_REJECT_CV_GAP: run_cv_gap_rejection_batch,
}


def enqueue_workable_op(
    *,
    organization_id: int,
    op_type: str,
    payload: dict,
    scope_id: int | None = None,
    job_kind: str | None = None,
    counters: dict | None = None,
    dispatch_key: str | None = None,
) -> int:
    """Record a BackgroundJobRun and enqueue the serialized runner task.

    Returns the durable job_run_id. No ATS task is published unless that row was
    persisted first, so every accepted operation has a meter and poll handle.
    The caller has already done any optimistic local flip (e.g. decision →
    processing) and committed, and must compensate it if this raises
    :class:`AtsJobRunPersistenceError`.
    """
    import json

    from ..models.background_job_run import JOB_KIND_DECISION_BATCH, JOB_KIND_WORKABLE_OP
    from ..platform.config import settings
    from ..platform.secrets import encrypt_text
    from .background_job_runs import (
        SCOPE_KIND_ORG,
        create_run,
        find_run_by_dispatch_key,
        mark_dispatched,
    )

    manual_operation_id, dispatch_intent_counters = None, {}
    if op_type == OP_MANUAL_OUTCOME:
        from .manual_outcome_lifecycle import validate_manual_outcome_payload
        *_, manual_operation_id = validate_manual_outcome_payload(payload)
    elif op_type == OP_POST_NOTE:
        note_identity = ats_note_dispatch_identity.prepare_note_dispatch_identity(
            payload, organization_id=organization_id, dispatch_key=dispatch_key
        )
        payload, manual_operation_id, dispatch_intent_counters = note_identity

    kind = job_kind or (
        JOB_KIND_DECISION_BATCH if op_type == OP_APPROVE_DECISIONS else JOB_KIND_WORKABLE_OP
    )
    stable_dispatch_key = str(dispatch_key or manual_operation_id or "").strip() or None
    if stable_dispatch_key is not None and len(stable_dispatch_key) > 200:
        raise AtsJobRunPersistenceError(op_type)
    replay_safe = op_type in {
        OP_MOVE_STAGE,
        OP_MANUAL_OUTCOME,
        OP_AUTO_REJECT,
        OP_POST_NOTE,
        OP_REJECT_CV_GAP,
    }
    run_counters = dict(counters or {"op_type": op_type})
    run_counters["op_type"] = op_type
    run_counters.update(dispatch_intent_counters)
    if op_type == OP_OVERRIDE_DECISION:
        # Deliberately persist only the non-secret coordination key, never the
        # override payload.  A watchdog can return the decision to HITL, but it
        # cannot replay a potentially non-idempotent recruiter action.
        run_counters["decision_id"] = int(payload["decision_id"])
    if replay_safe:
        run_counters["recovery_payload"] = encrypt_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            settings.SECRET_KEY,
        )
    if stable_dispatch_key is not None:
        existing_run_id = find_run_by_dispatch_key(
            stable_dispatch_key,
            organization_id=int(organization_id),
            kind=kind,
            op_type=op_type,
            expected_counters=dispatch_intent_counters,
        )
        if existing_run_id is not None:
            return existing_run_id
    job_run_id = create_run(
        kind=kind,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(scope_id if scope_id is not None else organization_id),
        organization_id=int(organization_id),
        counters=run_counters,
        status="dispatching" if replay_safe else "queued",
        dispatch_key=stable_dispatch_key,
    )
    if (
        isinstance(job_run_id, bool)
        or not isinstance(job_run_id, int)
        or job_run_id <= 0
    ):
        # A simultaneous producer may have inserted the same unique dispatch
        # receipt after the pre-query. Reuse it without a second broker publish.
        if stable_dispatch_key is not None:
            existing_run_id = find_run_by_dispatch_key(
                stable_dispatch_key,
                organization_id=int(organization_id),
                kind=kind,
                op_type=op_type,
                expected_counters=dispatch_intent_counters,
            )
            if existing_run_id is not None:
                return existing_run_id
        # ``create_run`` is intentionally best-effort for ordinary background
        # bookkeeping, but ATS writes require durable tracking. Fail before the
        # broker publish so a provider side effect can never run unmetered.
        raise AtsJobRunPersistenceError(op_type)
    from ..tasks.assessment_tasks import mark_workable_op_pending
    from ..tasks.workable_tasks import run_workable_op_task

    # Tell the periodic Workable syncs to yield the per-org mutex so this
    # user-facing write isn't starved behind a long candidate sync.
    mark_workable_op_pending(int(organization_id))
    try:
        run_workable_op_task.apply_async(
            kwargs={
                "job_run_id": job_run_id,
                "organization_id": int(organization_id),
                "op_type": op_type,
                "payload": payload,
            }
        )
    except Exception as exc:
        if op_type == OP_OVERRIDE_DECISION:
            reason = (
                "Returned to queue: the ATS override could not be delivered to "
                "the background worker. No ATS side effect was replayed; review "
                "the decision and try again."
            )
            outcome = compensate_override_delivery_loss(
                organization_id=int(organization_id),
                decision_id=int(payload["decision_id"]),
                job_run_id=job_run_id,
                reason=reason,
                error_code="initial_queue_unavailable",
                # If an ambiguous broker response already reached a worker and
                # it won the running claim, do not race or undo that live task.
                allowed_run_statuses=("queued",),
            )
            logger.error(
                "ATS override broker kick failed; compensation status=%s "
                "run_id=%s decision_id=%s error_type=%s",
                outcome.get("status"),
                job_run_id,
                payload.get("decision_id"),
                type(exc).__name__,
            )
            if outcome.get("status") in {
                "compensated",
                "already_terminal_or_active",
            }:
                return job_run_id
        if not replay_safe or job_run_id is None:
            raise
        # The durable dispatching row is the outbox. Beat will replay this
        # stable, receipt-keyed operation; the request can return the already-
        # committed local state without losing the remote update.
        logger.error(
            "ATS op broker kick failed; durable recovery will replay "
            "run_id=%s error_type=%s",
            job_run_id,
            type(exc).__name__,
        )
    else:
        if replay_safe:
            mark_dispatched(job_run_id)
    return job_run_id


def execute_op(
    db: Session, *, organization_id: int, op_type: str, payload: dict,
    should_yield: Callable[[], bool] | None = None,
) -> dict:
    handler = _HANDLERS.get(op_type)
    if handler is None:
        raise ValueError(f"unknown workable op_type={op_type!r}")
    runtime_payload = {**payload, "_should_yield": should_yield} if should_yield else payload
    return handler(db, int(organization_id), runtime_payload)


def surface_op_failure(
    db: Session, *, organization_id: int, op_type: str, payload: dict, error: WorkableWritebackError
) -> None:
    """Op-specific terminal-failure surfacing after retries are exhausted (or a
    non-retriable failure). Best-effort; never raises. Each op leaves a visible
    trail so a dropped Workable write is never silent."""
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    provider_slug, provider_label = _active_ats_label(
        db, int(organization_id), payload
    )
    note = (
        f"{provider_label} didn't accept the update after several tries. "
        f"{error.message}"
    )
    try:
        if op_type == OP_POST_NOTE:
            # The exact note lifecycle already records its own provider-aware,
            # attempt-specific terminal event. The generic shell still fails
            # the BackgroundJobRun, but must not append a duplicate event.
            return
        if op_type == OP_AUTO_REJECT:
            from .auto_reject_op import surface_auto_reject_failure
            surface_auto_reject_failure(
                db, organization_id=organization_id, payload=payload, error=error
            )
            return
        if op_type == OP_OVERRIDE_DECISION:
            _requeue_decision(db, int(payload["decision_id"]), int(organization_id), note=note)
            return
        if op_type == OP_APPROVE_DECISIONS:
            # The approve batch never ran (e.g. lock timeout) — return every
            # decision to the queue. Its payload carries ``decision_ids``, not
            # an ``application_id``, so without this the rows were stranded in
            # 'processing' forever (no approver, never completed).
            for d_id in (payload.get("decision_ids") or []):
                _requeue_decision(db, int(d_id), int(organization_id), note=note)
            return
        application_id = payload.get("application_id")
        if application_id is None:
            return
        app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
            )
            .first()
        )
        if app is None:
            return
        if op_type == OP_MANUAL_OUTCOME:
            from .manual_outcome_lifecycle import surface_manual_outcome_failure

            provider_evidence = (
                {"provider_called": error.provider_called}
                if hasattr(error, "provider_called")
                else {}
            )
            if surface_manual_outcome_failure(
                db,
                app,
                payload,
                error_code=error.code,
                error_message=error.message,
                **provider_evidence,
            ):
                return
        event_prefix = provider_slug if provider_slug in {"workable", "bullhorn"} else "ats"
        event_type = {
            OP_MOVE_STAGE: f"{event_prefix}_move_stage_failed",
            OP_MANUAL_OUTCOME: f"{event_prefix}_writeback_failed",
            OP_POST_NOTE: f"{event_prefix}_writeback_failed",
        }.get(op_type, f"{event_prefix}_writeback_failed")
        append_application_event(
            db,
            app=app,
            event_type=event_type,
            actor_type="system",
            reason=note,
            metadata={
                "op_type": op_type,
                "code": error.code,
                "source": "workable_op_runner",
                "ats": provider_slug,
            },
        )
        db.commit()
    except Exception:  # pragma: no cover — surfacing must never raise
        logger.exception("surface_op_failure raised for op_type=%s", op_type)
        try:
            db.rollback()
        except Exception:
            pass
