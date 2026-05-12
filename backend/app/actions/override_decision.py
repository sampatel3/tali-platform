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
