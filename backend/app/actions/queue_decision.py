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
from ..models.candidate_application_event import CandidateApplicationEvent
from .types import ACTOR_AGENT, Actor


def _capture_token_spend(
    db: Session, *, agent_run_id: int | None
) -> dict:
    """Discipline §8.5: roll up usage_events for this agent_run_id.

    Defers to ``token_spend_aggregator.aggregate`` which returns an
    empty dict on any failure or when no events match.
    """
    try:
        from ..agent_runtime import token_spend_aggregator
        return token_spend_aggregator.aggregate(db, agent_run_id=agent_run_id)
    except Exception:
        return {}


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
    # Discipline §8.5: roll up usage_events for this agent_run_id into
    # a single token_spend JSON blob on the decision row. Empty dict on
    # any failure — never blocks the queue.
    token_spend = _capture_token_spend(db, agent_run_id=actor.agent_run_id)

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
        token_spend=token_spend,
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

    # Phase 2 §6.7: one consolidated Graphiti episode per decision.
    # Folds the four sub-agent scores into the decision body so we get
    # one LLM extraction pass per decision instead of one per score —
    # keeps Graphiti billing bounded to the decision volume. Failure
    # is logged and ignored; the Postgres row is the source of truth.
    _emit_decision_episode_safe(db, decision=decision)

    # Also record a CandidateApplicationEvent so the per-role
    # /agent/status endpoint's `last_activity` reflects this decision
    # the moment it's queued — that's what the AgentBar tick reads.
    # Without this, AgentBar shows "Idle · waiting for new candidates"
    # even when the agent has just queued work, because formatTick
    # only sees pipeline_stage_changed / outcome_changed events that
    # are written by approve_decision *after* recruiter action.
    db.add(
        CandidateApplicationEvent(
            application_id=application_id,
            organization_id=organization_id,
            event_type="agent_decision_queued",
            actor_type="agent",
            actor_id=actor.agent_run_id,
            reason=f"Queued {decision_type.replace('_', ' ')}",
            idempotency_key=f"agent_decision_queued:{decision.id}",
            event_metadata={"decision_id": int(decision.id), "decision_type": decision_type},
        )
    )
    return decision


def _emit_decision_episode_safe(db: Session, *, decision: AgentDecision) -> None:
    """Best-effort consolidated decision episode emit. Never raises.

    Looks up candidate + role context inline so the orchestrator caller
    doesn't have to thread them through.
    """
    try:
        from ..candidate_graph import agent_episodes
        from ..models.candidate import Candidate
        from ..models.candidate_application import CandidateApplication

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == decision.application_id)
            .one_or_none()
        )
        if app is None:
            return
        candidate = (
            db.query(Candidate).filter(Candidate.id == app.candidate_id).one_or_none()
        )
        full_name = candidate.full_name if candidate is not None else None
        candidate_id = int(candidate.id) if candidate is not None else int(app.candidate_id)
        agent_episodes.emit_decision_event(
            organization_id=int(decision.organization_id),
            candidate_full_name=full_name,
            candidate_taali_id=candidate_id,
            application_id=int(decision.application_id),
            role_id=int(decision.role_id),
            decision_id=int(decision.id),
            recommended_action=str(decision.recommendation),
            confidence=float(decision.confidence or 0.0),
            policy_revision_id=None,
            reasoning=str(decision.reasoning or ""),
            created_at=decision.created_at or _now(),
        )
    except Exception:
        import logging
        logging.getLogger("taali.actions.queue_decision").warning(
            "decision episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
            exc_info=False,
        )


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
