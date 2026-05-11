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

    # Evidence validation (governance). Runs after the row is created.
    # Permissive: a failed validation does not refuse the queue — it
    # records the failure so the recruiter sees a warning badge and
    # audit queries can pull the bad evidence out later. Import here
    # to avoid a circular dep between actions and agent_runtime.
    try:
        from ..agent_runtime.decision_evidence import (
            validate_agent_decision_evidence,
        )

        outcome = validate_agent_decision_evidence(decision, db)
        decision.validation_status = outcome.status
        decision.validation_failures = (
            outcome.failures if outcome.failures else None
        )
        db.flush()
    except Exception:  # pragma: no cover — validator must never crash queueing
        import logging

        logging.getLogger("taali.actions.queue_decision").exception(
            "evidence validator raised; decision queued without validation status"
        )

    return decision
