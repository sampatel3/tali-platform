"""Recruiter approves a queued ``AgentDecision``.

Resolves the queue row to ``approved`` and dispatches the underlying
action with ``actor=recruiter`` so the audit row records *the recruiter*
as the one who made the change — with metadata pointing back to the
agent's reasoning and run id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from . import advance_stage, reject_application, resend_assessment_invite, send_assessment
from ._decision_side_effects import apply_decision_side_effects
from .types import ACTOR_RECRUITER, Actor


_REJECT_DECISION_TYPES = ("reject", "skip_assessment_reject")


def _accept_for_processing(
    db: Session, *, organization_id: int, decision_id: int, note: Optional[str]
) -> AgentDecision:
    """Lock a pending decision and flip it to ``processing``. Raises 404/409.

    No commit — the caller commits the whole batch's flips at once.
    """
    q = db.query(AgentDecision).filter(
        AgentDecision.id == decision_id,
        AgentDecision.organization_id == organization_id,
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    decision = q.first()
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not pending",
        )
    decision.status = "processing"
    if note is not None:
        decision.resolution_note = note
    return decision


def enqueue_batch(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_ids: list[int],
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    workable_target_stages: Optional[dict[str, str]] = None,
) -> dict:
    """Accept a batch of approvals for background processing.

    A whole request — single approve or a 100-row bulk approve — becomes ONE
    background job. Each valid pending decision is flipped to ``processing``
    (so the Hub shows it greyed/in-flight instead of letting the recruiter
    double-click), a single ``BackgroundJobRun`` (kind ``decision_batch``) is
    recorded for Settings → Background jobs, and ONE ``process_decision_batch``
    task drains the Workable writebacks sequentially (serialized per org, so a
    big batch can't breach the rate limit). A decision whose writeback fails is
    returned to the queue by the task — never lost.

    The only synchronous work is the status flip + bookkeeping (no Workable
    calls), so approving 100 decisions returns immediately.

    ``workable_target_stages`` is the per-role advance-stage map (``role_id``
    string → Workable stage) for a multi-role bulk approve; ``workable_target_stage``
    is the single-stage fallback used by ``enqueue_one``. The batch handler
    resolves each advance decision's stage from the map first, then the
    fallback. Roles in neither advance on Tali's internal stage only.

    Returns ``{"job_run_id", "accepted": [ids], "failures": [{decision_id, error}]}``.
    """
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="approve is recruiter-only")

    requested = list(dict.fromkeys(int(x) for x in decision_ids))
    accepted: list[int] = []
    failures: list[dict] = []
    for decision_id in requested:
        try:
            _accept_for_processing(
                db, organization_id=int(organization_id), decision_id=decision_id, note=note
            )
            accepted.append(decision_id)
        except HTTPException as exc:
            failures.append(
                {
                    "decision_id": decision_id,
                    "status_code": exc.status_code,
                    "error": str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
                }
            )
    # Commit all flips together so the worker (separate session) sees them.
    db.commit()

    job_run_id = None
    if accepted:
        from ..services.workable_op_runner import (
            OP_APPROVE_DECISIONS,
            AtsJobRunPersistenceError,
            enqueue_workable_op,
        )

        try:
            job_run_id = enqueue_workable_op(
                organization_id=int(organization_id),
                op_type=OP_APPROVE_DECISIONS,
                payload={
                    "decision_ids": accepted,
                    "user_id": int(actor.user_id) if actor.user_id else None,
                    "note": note,
                    "workable_target_stage": workable_target_stage,
                    "workable_target_stages": workable_target_stages or None,
                },
                # ``decision_ids`` lets the watchdog (expire_stuck_decision_batches)
                # return exactly this batch's rows to the queue if the worker is
                # killed mid-run. Overwritten by result counters on completion, so
                # it only persists while the run is in-flight — which is all the
                # watchdog needs.
                counters={
                    "total": len(accepted),
                    "succeeded": 0,
                    "requeued": 0,
                    "failed": 0,
                    "decision_ids": accepted,
                },
            )
        except AtsJobRunPersistenceError:
            # The optimistic processing flip is already committed so the
            # worker can see it. If its durable tracking row cannot be created,
            # fail closed before publish and return every untouched decision to
            # HITL instead of stranding it in an unpollable processing state.
            reason = (
                "Returned to queue: Taali could not create durable tracking "
                "for the ATS operation. No provider update was sent; try again."
            )
            rows = (
                db.query(AgentDecision)
                .filter(
                    AgentDecision.organization_id == int(organization_id),
                    AgentDecision.id.in_(accepted),
                    AgentDecision.status == "processing",
                )
                .all()
            )
            for row in rows:
                row.status = "pending"
                row.resolution_note = reason[:500]
            db.commit()
            raise
    return {"job_run_id": job_run_id, "accepted": accepted, "failures": failures}


def enqueue_one(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
) -> AgentDecision:
    """Single-decision wrapper over ``enqueue_batch`` that preserves the
    route's 404/409 semantics and returns the (now ``processing``) decision."""
    result = enqueue_batch(
        db,
        actor,
        organization_id=organization_id,
        decision_ids=[decision_id],
        note=note,
        workable_target_stage=workable_target_stage,
    )
    if int(decision_id) not in result["accepted"]:
        failure = next(
            (f for f in result["failures"] if f["decision_id"] == int(decision_id)),
            None,
        )
        raise HTTPException(
            status_code=(failure or {}).get("status_code", 409),
            detail=(failure or {}).get("error", "could not accept decision"),
        )
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == int(organization_id),
        )
        .first()
    )
    return decision


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    collect_side_effects: Optional[dict] = None,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="approve is recruiter-only")

    # C2: row-level lock on the decision. Two recruiters approving the
    # same pending decision in the same second would otherwise both pass
    # the ``status != "pending"`` check and both dispatch the underlying
    # action — double Workable POST, double pipeline-stage event, double
    # candidate email. ``with_for_update`` blocks the second request
    # until the first commits; it then sees ``status='approved'`` and
    # 409s. SQLite tests ignore the row lock (no NOWAIT support) which
    # is fine since the race only matters in real production traffic.
    decision_query = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        decision_query = decision_query.with_for_update()
    decision = decision_query.first()
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    # ``reverted_for_feedback`` is a taught-but-not-yet-resolved decision — the
    # corrected row can then be approved/overridden, so it stays actionable
    # alongside ``pending``. ``processing`` is accepted because the async
    # dispatch path flips the row pending→processing before enqueuing the
    # background task that calls run().
    if decision.status not in ("pending", "reverted_for_feedback", "processing"):
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not actionable",
        )

    metadata = {
        "agent_decision_id": int(decision.id),
        "agent_run_id": int(decision.agent_run_id) if decision.agent_run_id else None,
        "agent_reasoning": decision.reasoning,
        "model_version": decision.model_version,
        "prompt_version": decision.prompt_version,
    }
    reason = (note or "").strip() or f"Approved agent recommendation #{decision.id}"
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(decision.application_id),
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    org = (
        db.query(Organization).filter(Organization.id == organization_id).first()
        if app is not None
        else None
    )
    role = getattr(app, "role", None) if app is not None else None

    # "Did this approval freshly reject the candidate?" — gates the background
    # Workable disqualify so an already-rejected candidate isn't re-processed.
    # (Taali never emails the candidate; the ATS owns job comms.) Set in the
    # reject branch below.
    reject_notify = False

    if decision.decision_type == "advance_to_interview":
        advance_stage.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            to_stage="advanced",
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata=metadata,
        )
    elif decision.decision_type in _REJECT_DECISION_TYPES:
        prev_outcome = (
            getattr(app, "application_outcome", None) if app is not None else None
        )
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata={**metadata, "decision_type": decision.decision_type},
            defer_notify=True,
        )
        reject_notify = bool(
            app is not None
            and prev_outcome != "rejected"
            and getattr(app, "application_outcome", None) == "rejected"
        )
    elif decision.decision_type == "send_assessment":
        # Evidence (set when the agent queued the decision) may carry the
        # task_id / duration_minutes the agent picked. Fall back to the
        # send_assessment defaults when absent.
        ev = decision.evidence or {}
        send_result = send_assessment.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            task_id=int(ev["task_id"]) if ev.get("task_id") is not None else None,
            duration_minutes=int(ev.get("duration_minutes") or 90),
        )
        # send_assessment can no-op (misconfigured / insufficient_credits /
        # blocked / already_exists). "queued" means a durable delivery intent
        # exists; provider success will perform the invited transition. Anything
        # else must NOT close the
        # decision as approved (it never sent), so raise a clear, actionable
        # error. The approve runner returns the decision to the queue with
        # this message instead of silently looping (mirrors the override path).
        send_status = getattr(send_result, "status", None)
        if send_status not in ("queued", "sent", "already_exists"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Couldn't send the assessment (status={send_status!r}): "
                    f"{getattr(send_result, 'detail', None) or 'no assessment was sent'}. "
                    "Link an assessment task to this role, or use Skip & advance instead."
                ),
            )
    elif decision.decision_type == "resend_assessment_invite":
        ev = decision.evidence or {}
        assessment_id = ev.get("assessment_id")
        if assessment_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"resend_assessment_invite decision {decision.id} is missing "
                    "evidence.assessment_id — cannot dispatch."
                ),
            )
        resend_result = resend_assessment_invite.run(
            db,
            actor,
            organization_id=organization_id,
            assessment_id=int(assessment_id),
        )
        resend_status = getattr(resend_result, "status", None)
        if resend_status in {"blocked", "voided", "no_candidate"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Couldn't resend the assessment (status={resend_status!r}): "
                    f"{getattr(resend_result, 'detail', None) or 'no invite was queued'}."
                ),
            )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"unknown decision_type={decision.decision_type!r}",
        )

    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = actor.user_id
    decision.resolution_note = note
    decision.human_disposition = "approved"

    if role is not None:
        try:
            from ..agent_runtime import calibration

            calibration.save(db, role=role, updates={"decisions_approved": 1})
        except Exception:
            import logging

            logging.getLogger("taali.actions.approve_decision").exception(
                "approval calibration counter failed (decision_id=%s)", decision.id
            )

    # Realised-outcome learning. The pipeline transition fired by the action
    # dispatch above already ran the outcome_learning hooks, but at that point
    # this decision was still ``processing`` — so the hooks' approved-decision
    # lookup found nothing. Now that it's stamped ``approved`` we record the
    # outcome against it directly. Best-effort: calibration bookkeeping must
    # never block an approval.
    if app is not None:
        try:
            from ..agent_runtime import outcome_learning

            outcome_learning.record_outcome_for_approved_decision(
                db, decision=decision, application=app,
            )
        except Exception:
            import logging

            logging.getLogger("taali.actions.approve_decision").exception(
                "realised-outcome recording failed (decision_id=%s)", decision.id,
            )

    # Best-effort side effects (Workable writeback + recruiter-action graph
    # episode). By default they run inline (agent runs, tests). When the
    # caller passes ``collect_side_effects`` — the approve / bulk-approve
    # routes do — we skip the inline work and hand the route what it needs to
    # enqueue the deferred Celery task post-commit, so the recruiter's click
    # returns immediately instead of waiting on slow Workable / LLM calls.
    if collect_side_effects is None:
        apply_decision_side_effects(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            note=note,
            workable_target_stage=workable_target_stage,
            reject_notify=reject_notify,
        )
    else:
        collect_side_effects["reject_notify"] = reject_notify

    return decision
