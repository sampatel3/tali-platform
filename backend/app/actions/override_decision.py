"""Recruiter overrides a queued ``AgentDecision`` and picks an alternative.

Closes the loop. The recruiter doesn't just disagree — they say what
should happen instead, and the underlying action runs server-side with
``actor=recruiter`` so the audit trail records the recruiter as the
one who took the action. ``override_action`` is one of:

  - ``reject``                  → reject_application.run
  - ``advance``                 → advance_stage.run (to next stage)
  - ``send_assessment``         → send_assessment.run
  - ``hold`` / None             → no side effect (legacy "just disagree")

``skip_assessment_advance`` is handled SEPARATELY (not in ``run``'s dispatch):
the route calls ``reclassify_to_advance_queue`` so the card moves into the
advance queue rather than advancing + writing Workable immediately. The
``run`` branch for it is kept only for back-compat with any in-flight queued
override op.

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


def reclassify_to_advance_queue(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
) -> AgentDecision:
    """"Skip & advance" — reclassify a pending card into the advance queue.

    Instead of advancing + writing Workable immediately (which needs a target
    stage the Hub card can't reliably collect — an empty/failed stage list used
    to advance Tali-internally with no Workable move at all), this turns the
    pending decision into a pending ``advance_to_interview`` decision. It then
    sits in the advance queue, where the normal advance flow collects the
    Workable stage and posts the summary comment when the recruiter approves it.

    No Workable write and no stage transition happen here; the decision stays
    PENDING. Recruiter-only. Reclassifying a row that is already
    ``advance_to_interview`` is a no-op.
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
    if decision.status not in ("pending", "reverted_for_feedback", "processing"):
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not actionable",
        )

    if decision.decision_type != "advance_to_interview":
        from .queue_decision import _compute_dedup_key

        prior_type = decision.decision_type
        decision.decision_type = "advance_to_interview"
        decision.recommendation = "advance_to_interview"
        ev = dict(decision.evidence) if isinstance(decision.evidence, dict) else {}
        ev["reclassified_from"] = prior_type
        ev["reclassified_by"] = "recruiter_skip_assessment_advance"
        if note:
            ev["recruiter_skip_note"] = note
        decision.evidence = ev
        # Refresh the cross-cycle dedup key for the new type so a later agent
        # tick doesn't read the advance as a brand-new decision to re-queue.
        try:
            decision.decision_dedup_key = _compute_dedup_key(
                db,
                application_id=int(decision.application_id),
                decision_type="advance_to_interview",
            )
        except Exception:  # pragma: no cover — dedup is best-effort
            pass

    # Stays PENDING (not a resolution): the recruiter approves the advance —
    # and picks the Workable stage — from the advance queue.
    decision.status = "pending"
    db.commit()
    return decision


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
    # The non-replayable delivery compensator may have returned this decision to
    # ``pending`` in a separate short-lived session when the broker rejected the
    # initial publish. Refresh so the response never falsely reports processing.
    db.refresh(decision)
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
    # Workable disqualify (Taali never emails the candidate; the ATS owns job
    # comms). Set in the reject branch below.
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
        if send_status not in ("queued", "sent", "already_exists"):
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

    if role is not None:
        try:
            from ..agent_runtime import calibration

            calibration.save(db, role=role, updates={"decisions_overridden": 1})
        except Exception:
            import logging

            logging.getLogger("taali.actions.override_decision").exception(
                "override calibration counter failed (decision_id=%s)", decision.id
            )

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
