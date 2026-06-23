"""Generic serialized runner for ALL Workable write-backs.

Every recruiter/system action that writes to Workable (decision approve / bulk
/ override, hand-back stage move, manual outcome change, free-form note) routes
through here instead of calling Workable inline on the request thread. The
goals, uniform across all of them:

- **Serialized per org** — one Workable conversation per org at a time (shared
  ``_acquire_workable_org_mutex``), so a burst of actions can't breach the rate
  limit.
- **Background + tracked** — each request becomes a ``BackgroundJobRun`` (kind
  ``decision_batch`` for Hub batches, ``workable_op`` for single ops) visible
  in Settings → Background jobs.
- **Retried + never dropped** — a transient failure (429/5xx → ``api_error``)
  retries with backoff; on exhaustion the op surfaces (re-queues the decision /
  records a ``workable_*_failed`` event) instead of silently vanishing.

This module holds the op handlers + the dispatch (``execute_op`` /
``surface_op_failure``). The Celery shell that owns the mutex, the job-run
bookkeeping and the retry/backoff lives in
``app.tasks.workable_tasks.run_workable_op_task``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from .workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)

logger = logging.getLogger("taali.workable_op_runner")


# Op type constants — also the dispatch keys.
OP_APPROVE_DECISIONS = "approve_decisions"
OP_OVERRIDE_DECISION = "override_decision"
OP_MOVE_STAGE = "move_stage"
OP_MANUAL_OUTCOME = "manual_outcome"
OP_POST_NOTE = "post_note"

# Override actions whose Workable write is a safely-replayable state change
# (disqualify / stage move) — gated so a failure re-queues. send_assessment /
# hold are NOT gated (email side-effect / no-op).
_GATED_OVERRIDE_ACTIONS = frozenset({"reject", "advance", "skip_assessment_advance"})
# Decision types whose approval Workable write is safely replayable (gated).
_GATED_DECISION_TYPES = frozenset({"reject", "skip_assessment_reject", "advance_to_interview"})


def _recruiter_actor(user_id: int | None):
    from ..actions.types import ACTOR_RECRUITER, Actor

    return Actor(type=ACTOR_RECRUITER, user_id=int(user_id) if user_id else None)


# ---------------------------------------------------------------------------
# Op handlers. Each takes (db, organization_id, payload) and returns a result
# dict. Single-op handlers may raise WorkableWritebackError (the Celery shell
# turns a retriable one into a retry, and a terminal one into
# ``surface_op_failure``). The batch handler is self-contained: it commits per
# decision and never raises, so one bad row can't fail the whole batch.
# ---------------------------------------------------------------------------


def _enqueue_best_effort_side_effects(decision_id: int) -> None:
    """Defer a batch-approved decision's best-effort side effects (Workable
    summary note + recruiter-action graph episode) to the Celery task.

    The batch already ran the GATED Workable writeback inline + strict, so this
    only covers the best-effort steps — run off the serialized per-org mutex so
    a 100-row batch isn't doing a Graphiti/Voyage LLM call per decision while
    holding it. Best-effort: a failed enqueue must never fail the approval that
    has already committed.
    """
    try:
        from ..tasks.decision_tasks import apply_decision_side_effects

        apply_decision_side_effects.delay(int(decision_id), steps="best_effort")
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "failed to enqueue best-effort side effects decision_id=%s",
            decision_id,
            exc_info=True,
        )


def _requeue_decision(db: Session, decision_id: int, organization_id: int, *, note: str) -> None:
    """Return a processing decision to the Hub queue (status → pending)."""
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


def _op_approve_decisions(db: Session, organization_id: int, payload: dict) -> dict:
    """Drain a batch of approved decisions sequentially (self-contained).

    Each decision's local change commits only after its GATED Workable write
    confirms (gated types); a decision whose writeback fails is returned to the
    queue and the batch keeps going.

    Only the gated writeback runs inline here. The best-effort side effects
    (Workable summary note + recruiter-action graph episode — a Graphiti/Voyage
    LLM call) are deferred per decision to ``app.tasks.decision_tasks`` so the
    batch isn't doing ~3-8s of slow work per decision while holding the per-org
    mutex (a 100-row batch was taking 5-8+ minutes, stranding cards on
    "Processing…" and exposed to a deploy SIGKILL mid-run).
    """
    from ..actions import approve_decision as approve_decision_action

    ids = [int(x) for x in (payload.get("decision_ids") or [])]
    note = payload.get("note")
    workable_target_stage = payload.get("workable_target_stage")
    # Per-role advance-stage map (role_id string → Workable stage). A bulk
    # approve spanning roles carries one stage per role; the single fallback
    # above covers enqueue_one / single approve.
    workable_target_stages = payload.get("workable_target_stages") or {}
    actor = _recruiter_actor(payload.get("user_id"))

    counters = {"total": len(ids), "succeeded": 0, "requeued": 0, "failed": 0, "skipped": 0}
    for decision_id in ids:
        decision = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id == decision_id,
                AgentDecision.organization_id == organization_id,
            )
            .first()
        )
        if decision is None or decision.status != "processing":
            # Already resolved / requeued elsewhere (e.g. approved by an earlier
            # overlapping batch) — idempotent skip. Counted separately so a run
            # with succeeded < total reads as "X approved, Y already resolved"
            # instead of looking like a partial failure.
            counters["skipped"] += 1
            continue
        stage = (
            workable_target_stages.get(str(decision.role_id))
            if decision.role_id is not None
            else None
        ) or workable_target_stage
        gated = decision.decision_type in _GATED_DECISION_TYPES
        try:
            # ``defer_best_effort_side_effects`` keeps only the GATED Workable
            # writeback inline (run under ``strict_workable_writes`` for gated
            # types so a failure re-queues) and lets us enqueue the best-effort
            # summary note + graph episode below — so the batch drains fast and
            # releases the per-org mutex instead of doing a Graphiti/Voyage LLM
            # call per decision while holding it.
            if gated:
                with strict_workable_writes():
                    approve_decision_action.run(
                        db,
                        actor,
                        organization_id=int(organization_id),
                        decision_id=int(decision_id),
                        note=note,
                        workable_target_stage=stage,
                        defer_best_effort_side_effects=True,
                    )
            else:
                approve_decision_action.run(
                    db,
                    actor,
                    organization_id=int(organization_id),
                    decision_id=int(decision_id),
                    note=note,
                    workable_target_stage=stage,
                    defer_best_effort_side_effects=True,
                )
            db.commit()
            counters["succeeded"] += 1
            # Post-commit: hand the slow best-effort effects to Celery (the
            # decision row is now committed, so the deferred task re-reads it).
            _enqueue_best_effort_side_effects(int(decision_id))
        except WorkableWritebackError as exc:
            db.rollback()
            _requeue_decision(
                db,
                decision_id,
                organization_id,
                note=f"Returned to queue: Workable writeback failed ({exc.code}). {exc.message}",
            )
            counters["requeued"] += 1
        except HTTPException as exc:
            # A deterministic, expected action failure (e.g. send_assessment on a
            # role with no linked task, missing resend evidence). Re-queue with
            # the clear message so the recruiter sees *why* on the card and can
            # act, rather than a generic "unexpected error".
            db.rollback()
            _requeue_decision(
                db,
                decision_id,
                organization_id,
                note=f"Returned to queue: {exc.detail}",
            )
            counters["requeued"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not halt the batch
            db.rollback()
            logger.exception("approve_decisions: unexpected error decision_id=%s", decision_id)
            _requeue_decision(
                db,
                decision_id,
                organization_id,
                note=f"Returned to queue after an unexpected error: {str(exc)[:180]}",
            )
            counters["failed"] += 1
    return counters


def _op_override_decision(db: Session, organization_id: int, payload: dict) -> dict:
    """Apply a single recruiter override, gated on Workable for state-change
    actions (reject / advance / skip-advance). Raises on Workable failure so
    the shell retries / re-queues."""
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
    if decision is None or decision.status != "processing":
        return {"status": "skipped", "reason": "not_processing", "decision_id": decision_id}

    actor = _recruiter_actor(payload.get("user_id"))
    override_action = payload.get("override_action")
    gated = override_action in _GATED_OVERRIDE_ACTIONS

    def _run():
        override_decision_action.run(
            db,
            actor,
            organization_id=int(organization_id),
            decision_id=decision_id,
            override_action=override_action,
            note=payload.get("note"),
            workable_target_stage=payload.get("workable_target_stage"),
        )

    if gated:
        with strict_workable_writes():
            _run()
    else:
        _run()
    db.commit()
    return {"status": "ok", "decision_id": decision_id}


def _op_move_stage(db: Session, organization_id: int, payload: dict) -> dict:
    """Hand a candidate back to a Workable stage. Gated: Tali's stage advances
    only after the Workable move confirms."""
    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
        is_post_handover_workable_stage,
        map_legacy_status_to_pipeline,
        transition_stage,
    )
    from ..models.organization import Organization
    from ..models.role import Role
    from .workable_actions_service import move_candidate_in_workable

    application_id = int(payload["application_id"])
    target_stage = str(payload.get("target_stage") or "").strip()
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None or not app.workable_candidate_id:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    role = db.query(Role).filter(Role.id == app.role_id).first() if app.role_id else None

    with strict_workable_writes():
        move_candidate_in_workable(
            org=org,
            candidate_id=str(app.workable_candidate_id),
            target_stage=target_stage,
            role=role,
        )
    app.workable_stage = target_stage
    # Local-write-wins: stamp so the candidate sync won't revert this fresh move.
    app.workable_stage_local_write_at = datetime.now(timezone.utc)
    append_application_event(
        db,
        app=app,
        event_type="workable_moved",
        actor_type="recruiter",
        actor_id=user_id,
        reason=reason or "Recruiter handed candidate back to Workable",
        metadata={"target_stage": target_stage, "workable_candidate_id": app.workable_candidate_id},
    )
    mapped_stage, _ = map_legacy_status_to_pipeline(target_stage)
    if mapped_stage == "advanced" and is_post_handover_workable_stage(target_stage):
        transition_stage(
            db,
            app=app,
            to_stage="advanced",
            source="recruiter",
            actor_type="recruiter",
            actor_id=user_id,
            reason=f"Handed back to Workable: {target_stage}",
            metadata={"workable_target_stage": target_stage},
            idempotency_key=f"workable_handback:{app.id}:{target_stage}",
        )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def _op_manual_outcome(db: Session, organization_id: int, payload: dict) -> dict:
    """Mirror a recruiter's manual outcome change to Workable (disqualify on
    reject, revert on re-open). The local outcome already committed in the
    route — this is the (retried) Workable writeback only."""
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    from ..models.organization import Organization
    from ..models.role import Role
    from .workable_actions_service import (
        disqualify_candidate_in_workable,
        revert_candidate_disqualification_in_workable,
    )

    application_id = int(payload["application_id"])
    target_outcome = payload.get("target_outcome")
    reason = payload.get("reason")
    user_id = payload.get("user_id")
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None or not app.workable_candidate_id:
        return {"status": "skipped", "reason": "not_linked", "application_id": application_id}
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    role = db.query(Role).filter(Role.id == app.role_id).first() if app.role_id else None

    with strict_workable_writes():
        if target_outcome == "open":
            revert_candidate_disqualification_in_workable(org=org, app=app, role=role)
            event_type = "workable_reverted"
        else:
            disqualify_candidate_in_workable(org=org, app=app, role=role, reason=reason)
            event_type = "workable_disqualified"
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type="recruiter",
        actor_id=user_id,
        reason=reason or "Workable outcome synced",
        metadata={"workable_candidate_id": app.workable_candidate_id, "target_outcome": target_outcome},
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


def _op_post_note(db: Session, organization_id: int, payload: dict) -> dict:
    """Post a free-form note to the candidate's Workable activity feed."""
    from ..models.organization import Organization
    from .workable_actions_service import resolve_workable_actor_member_id
    from ..domains.integrations_notifications.adapters import build_workable_adapter
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    application_id = int(payload["application_id"])
    body = str(payload.get("body") or "").strip()
    user_id = payload.get("user_id")
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    if app is None or not app.workable_candidate_id or not body:
        return {"status": "skipped", "reason": "not_linked_or_empty", "application_id": application_id}
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    member_id = resolve_workable_actor_member_id(org, role=getattr(app, "role", None))
    if not member_id or not (org and getattr(org, "workable_access_token", None)):
        return {"status": "skipped", "reason": "not_configured", "application_id": application_id}

    adapter = build_workable_adapter(
        access_token=org.workable_access_token, subdomain=org.workable_subdomain
    )
    result = adapter.post_candidate_comment(
        candidate_id=str(app.workable_candidate_id), member_id=member_id, body=body
    )
    if not result.get("success"):
        # Surface as a retriable Workable failure so the shell retries.
        raise WorkableWritebackError(
            action="note", code="api_error", message=str(result.get("error") or "note post failed"), retriable=True
        )
    append_application_event(
        db,
        app=app,
        event_type="workable_note_posted",
        actor_type="recruiter",
        actor_id=user_id,
        reason="Recruiter note posted to Workable",
        metadata={"workable_candidate_id": app.workable_candidate_id},
    )
    db.commit()
    return {"status": "ok", "application_id": application_id}


_HANDLERS: dict[str, Callable[[Session, int, dict], dict]] = {
    OP_APPROVE_DECISIONS: _op_approve_decisions,
    OP_OVERRIDE_DECISION: _op_override_decision,
    OP_MOVE_STAGE: _op_move_stage,
    OP_MANUAL_OUTCOME: _op_manual_outcome,
    OP_POST_NOTE: _op_post_note,
}


def enqueue_workable_op(
    *,
    organization_id: int,
    op_type: str,
    payload: dict,
    scope_id: int | None = None,
    job_kind: str | None = None,
    counters: dict | None = None,
) -> int | None:
    """Record a BackgroundJobRun and enqueue the serialized runner task.

    Returns the job_run_id (None if bookkeeping failed — the task is still
    enqueued so the write isn't lost). The caller has already done any
    optimistic local flip (e.g. decision → processing) and committed.
    """
    from ..models.background_job_run import JOB_KIND_DECISION_BATCH, JOB_KIND_WORKABLE_OP
    from .background_job_runs import SCOPE_KIND_ORG, create_run

    kind = job_kind or (
        JOB_KIND_DECISION_BATCH if op_type == OP_APPROVE_DECISIONS else JOB_KIND_WORKABLE_OP
    )
    job_run_id = create_run(
        kind=kind,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=int(scope_id if scope_id is not None else organization_id),
        organization_id=int(organization_id),
        counters=counters or {"op_type": op_type},
        status="queued",
    )
    from ..tasks.assessment_tasks import mark_workable_op_pending
    from ..tasks.workable_tasks import run_workable_op_task

    # Tell the periodic Workable syncs to yield the per-org mutex so this
    # user-facing write isn't starved behind a long candidate sync.
    mark_workable_op_pending(int(organization_id))
    run_workable_op_task.apply_async(
        kwargs={
            "job_run_id": job_run_id,
            "organization_id": int(organization_id),
            "op_type": op_type,
            "payload": payload,
        }
    )
    return job_run_id


def execute_op(db: Session, *, organization_id: int, op_type: str, payload: dict) -> dict:
    handler = _HANDLERS.get(op_type)
    if handler is None:
        raise ValueError(f"unknown workable op_type={op_type!r}")
    return handler(db, int(organization_id), payload)


def surface_op_failure(
    db: Session, *, organization_id: int, op_type: str, payload: dict, error: WorkableWritebackError
) -> None:
    """Op-specific terminal-failure surfacing after retries are exhausted (or a
    non-retriable failure). Best-effort; never raises. Each op leaves a visible
    trail so a dropped Workable write is never silent."""
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    note = f"Workable writeback failed ({error.code}) after retries: {error.message}"
    try:
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
        event_type = {
            OP_MOVE_STAGE: "workable_move_stage_failed",
            OP_MANUAL_OUTCOME: "workable_writeback_failed",
            OP_POST_NOTE: "workable_writeback_failed",
        }.get(op_type, "workable_writeback_failed")
        append_application_event(
            db,
            app=app,
            event_type=event_type,
            actor_type="system",
            reason=note,
            metadata={"op_type": op_type, "code": error.code, "source": "workable_op_runner"},
        )
        db.commit()
    except Exception:  # pragma: no cover — surfacing must never raise
        logger.exception("surface_op_failure raised for op_type=%s", op_type)
        try:
            db.rollback()
        except Exception:
            pass
