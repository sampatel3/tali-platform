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
from . import advance_stage
from .types import ACTOR_RECRUITER, Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="approve is recruiter-only")

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
    }
    reason = (note or "").strip() or f"Approved agent recommendation #{decision.id}"

    if decision.decision_type == "advance_to_interview":
        advance_stage.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            to_stage="technical_interview",
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata=metadata,
        )
    elif decision.decision_type in ("reject", "skip_assessment_reject"):
        # Reject path lands in Phase 2 — this gate keeps the API surface
        # complete without silently no-op'ing on Phase 1.
        raise HTTPException(
            status_code=501,
            detail=f"approve for decision_type={decision.decision_type} not yet implemented (Phase 2)",
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
    return decision
