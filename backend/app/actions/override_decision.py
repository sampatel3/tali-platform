"""Recruiter overrides a queued ``AgentDecision``.

Marks the queue row ``overridden`` without performing any side effect —
the recruiter takes whatever action they want via the existing manual
UI flow (the override action is recorded for calibration so the agent
learns from disagreement).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from .types import ACTOR_RECRUITER, Actor


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

    decision.status = "overridden"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = actor.user_id
    decision.override_action = override_action
    decision.resolution_note = note
    decision.human_disposition = "overridden"
    return decision
