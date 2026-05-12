"""Insert a queued ``AgentDecision`` for recruiter approval.

Called only by the agent (via MCP tool). High-stakes decisions —
``advance_to_interview``, ``reject``, ``skip_assessment_reject`` — never
auto-execute; they queue here and surface in the recruiter's pending
panel for one-click approve or override.

Idempotency key ``{run_id}:{application_id}:{decision_type}`` prevents
the agent re-queuing the same decision on retry.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.role_support import get_application
from ..models.agent_decision import AGENT_DECISION_TYPES, AgentDecision
from .types import ACTOR_AGENT, Actor


def _capture_active_capabilities(
    db: Session,
    *,
    organization_id: int,
    decision_id: str,
    role_id: int | None,
) -> dict[str, bool]:
    """Snapshot every registered v10 capability for this decision.

    Captured at the moment the decision is queued — the resulting dict
    is what the audit query later relies on to reconstruct the runtime
    state. Failures here NEVER block decision queueing; an empty dict
    is the safe-degrade ("treat as v1/v2 era").
    """
    try:
        from ..capabilities import ALL_CAPABILITIES, get_shared
        return get_shared().snapshot(
            ALL_CAPABILITIES,
            db=db,
            organization_id=organization_id,
            decision_id=decision_id,
            role_id=role_id,
        )
    except Exception:
        return {}


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    decision_type: str,
    reasoning: str,
    evidence: Optional[dict[str, Any]] = None,
    confidence: Optional[float] = None,
    model_version: str,
    prompt_version: str,
    recommendation: Optional[str] = None,
) -> AgentDecision:
    if actor.type != ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="queue_decision is agent-only; recruiters take direct actions.",
        )
    if decision_type not in AGENT_DECISION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown decision_type={decision_type!r}",
        )
    if not (reasoning or "").strip():
        raise HTTPException(status_code=422, detail="reasoning is required")
    if actor.agent_run_id is None:
        raise HTTPException(status_code=422, detail="agent actor missing agent_run_id")

    # Validate the application belongs to the org+role.
    app = get_application(application_id, organization_id, db)
    if int(app.role_id) != int(role_id):
        raise HTTPException(
            status_code=422,
            detail=f"application {application_id} does not belong to role {role_id}",
        )

    idempotency_key = f"{actor.agent_run_id}:{application_id}:{decision_type}"
    active_capabilities = _capture_active_capabilities(
        db,
        organization_id=organization_id,
        decision_id=idempotency_key,
        role_id=role_id,
    )

    decision = AgentDecision(
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        agent_run_id=actor.agent_run_id,
        decision_type=decision_type,
        recommendation=recommendation or decision_type,
        status="pending",
        reasoning=reasoning.strip(),
        evidence=evidence,
        confidence=confidence,
        model_version=model_version,
        prompt_version=prompt_version,
        idempotency_key=idempotency_key,
        active_capabilities=active_capabilities,
    )
    db.add(decision)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(AgentDecision)
            .filter(AgentDecision.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return existing
        raise
    return decision
