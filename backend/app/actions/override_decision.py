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
from . import advance_stage, reject_application, send_assessment
from .types import ACTOR_RECRUITER, Actor


_OVERRIDE_DISPATCH_ACTIONS = {
    "reject",
    "advance",
    "skip_assessment_advance",
    "send_assessment",
}


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    override_action: Optional[str] = None,
    note: Optional[str] = None,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="override is recruiter-only")

    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not pending",
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

    # Dispatch the alternative action the recruiter picked. None / "hold"
    # are no-ops (legacy "just disagree" branch — kept for back-compat
    # while the UI rolls out).
    if override_action == "reject":
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=(note or "").strip() or f"Override reject on agent decision #{decision.id}",
            idempotency_key=idempotency,
            metadata={**metadata, "override_action": override_action},
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
        send_assessment.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            task_id=None,
            duration_minutes=90,
        )
    elif override_action and override_action not in _OVERRIDE_DISPATCH_ACTIONS and override_action != "hold":
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

    # Phase 2 §6.7: emit a recruiter-action episode. The override
    # action (what the recruiter manually did instead) rides in the
    # reason so the graph extractor can pick it up.
    try:
        from ..candidate_graph import agent_episodes
        reason_parts = []
        if override_action:
            reason_parts.append(f"override_action={override_action}")
        if note:
            reason_parts.append(note)
        agent_episodes.emit_recruiter_action_event(
            organization_id=int(organization_id),
            decision_id=int(decision.id),
            recruiter_id=int(actor.user_id) if actor.user_id else 0,
            action="override",
            reason=" | ".join(reason_parts) if reason_parts else None,
            happened_at=decision.resolved_at,
        )
    except Exception:
        import logging
        logging.getLogger("taali.actions.override_decision").warning(
            "recruiter-action episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
        )
    return decision
