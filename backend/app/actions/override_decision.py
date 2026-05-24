"""Recruiter overrides a queued ``AgentDecision`` and picks an alternative.

Closes the loop. The recruiter doesn't just disagree — they say what
should happen instead, and the underlying action runs server-side with
``actor=recruiter`` so the audit trail records the recruiter as the
one who took the action. ``override_action`` is one of:

  - ``reject``                  → reject_application.run
  - ``advance``                 → advance_stage.run (to next stage)
  - ``skip_assessment_advance`` → advance_stage.run + metadata flag
  - ``send_assessment``         → send_assessment.run
  - ``hold`` / None             → no side effect (legacy "just disagree")

The free-text ``note`` is the recruiter's "why" — fed to calibration
so the agent learns from the disagreement on top of the concrete
action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from . import advance_stage, reject_application, send_assessment
from ._decision_side_effects import apply_decision_side_effects
from .types import ACTOR_RECRUITER, Actor


_OVERRIDE_DISPATCH_ACTIONS = {
    "reject",
    "advance",
    "skip_assessment_advance",
    "send_assessment",
}
# Legacy override_action values that older clients pass — we still
# accept them as no-op overrides (status=overridden, no side effect on
# candidate) so the v0 "manual_review" UI path doesn't 422. Eventually
# the migrated clients drop these and we can tighten the allowlist.
_OVERRIDE_LEGACY_NOOP = {
    "manual_review",
    "hold",
}


def enqueue(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    override_action: Optional[str] = None,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
) -> AgentDecision:
    """Optimistically accept an override and run it via the serialized Workable
    runner. Flips the decision ``pending → processing`` (stays in the queue,
    greyed), records a BackgroundJobRun, and enqueues the override op — which
    for state-change actions (reject/advance/skip-advance) is gated on Workable
    and re-queues the decision on failure. Returns the ``processing`` decision.
    """
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="override is recruiter-only")

    q = db.query(AgentDecision).filter(
        AgentDecision.id == decision_id,
        AgentDecision.organization_id == organization_id,
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    decision = q.first()
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status not in ("pending", "reverted_for_feedback"):
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not actionable",
        )
    decision.status = "processing"
    if note is not None:
        decision.resolution_note = note
    decision.override_action = override_action
    db.commit()

    from ..services.workable_op_runner import OP_OVERRIDE_DECISION, enqueue_workable_op

    enqueue_workable_op(
        organization_id=int(organization_id),
        op_type=OP_OVERRIDE_DECISION,
        payload={
            "decision_id": int(decision_id),
            "user_id": int(actor.user_id) if actor.user_id else None,
            "override_action": override_action,
            "note": note,
            "workable_target_stage": workable_target_stage,
        },
    )
    return decision


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    override_action: Optional[str] = None,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    collect_side_effects: Optional[dict] = None,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="override is recruiter-only")

    # C2: row-level lock so two concurrent overrides don't both dispatch.
    # See approve_decision.run for the full reasoning.
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
    # ``reverted_for_feedback`` (taught) decisions stay actionable so a
    # recruiter can override the corrected row, not just freshly-pending ones.
    # ``processing`` is accepted because the async runner flips the row to it
    # before the background task calls run().
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
        "override_reason": note,
    }
    idempotency = f"override_decision:{decision.id}"

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

    # "Did this override freshly reject the candidate?" — gates the deferred
    # Workable disqualify / rejection email. Set in the reject branch below.
    reject_notify = False

    # Dispatch the alternative action the recruiter picked. None / "hold"
    # are no-ops (legacy "just disagree" branch — kept for back-compat
    # while the UI rolls out).
    if override_action == "reject":
        prev_outcome = (
            getattr(app, "application_outcome", None) if app is not None else None
        )
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=(note or "").strip() or f"Override reject on agent decision #{decision.id}",
            idempotency_key=idempotency,
            metadata={**metadata, "override_action": override_action},
            defer_notify=True,
        )
        reject_notify = bool(
            app is not None
            and prev_outcome != "rejected"
            and getattr(app, "application_outcome", None) == "rejected"
        )
    elif override_action in ("advance", "skip_assessment_advance"):
        advance_stage.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            to_stage="advanced",
            reason=(note or "").strip() or f"Override advance on agent decision #{decision.id}",
            idempotency_key=idempotency,
            metadata={**metadata, "override_action": override_action},
        )
    elif override_action == "send_assessment":
        send_result = send_assessment.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            task_id=None,
            duration_minutes=90,
        )
        # send_assessment can no-op (misconfigured / insufficient_credits /
        # already_exists / no_candidate / voided). Don't mark the decision
        # overridden in those cases — the candidate didn't get an email
        # and closing the queue row would lose the pending state without
        # actually doing the override (Codex #192).
        send_status = getattr(send_result, "status", None)
        if send_status not in ("sent", "already_exists"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"send_assessment override failed (status={send_status!r}). "
                    "Decision remains pending."
                ),
            )
    elif (
        override_action
        and override_action not in _OVERRIDE_DISPATCH_ACTIONS
        and override_action not in _OVERRIDE_LEGACY_NOOP
    ):
        raise HTTPException(
            status_code=422,
            detail=f"unknown override_action={override_action!r}",
        )

    decision.status = "overridden"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = actor.user_id
    decision.override_action = override_action
    decision.resolution_note = note
    decision.human_disposition = "overridden"

    # Best-effort side effects (Workable writeback + recruiter-action graph
    # episode). Run inline by default; deferred to a Celery task when the
    # route passes ``collect_side_effects`` so the recruiter's click returns
    # without waiting on slow Workable / LLM calls. The Workable summary note
    # is a no-op for the legacy hold / manual_review / None branch (verdict is
    # None) but the graph episode still fires for every override.
    if collect_side_effects is None:
        apply_decision_side_effects(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="overridden",
            override_action=override_action,
            note=note,
            workable_target_stage=workable_target_stage,
            reject_notify=reject_notify,
        )
    else:
        collect_side_effects["reject_notify"] = reject_notify

    return decision
