"""Close the feedback loop on agent decisions.

The agent's calibration today tracks recruiter approval rate (did the
recruiter agree when the agent queued a decision). That measures
*agreement at queue time* — not whether the agent's recommendations
turned out to be right. An agent that the recruiter trusts but whose
advances never reach interview, or whose rejects are later overruled,
is a less-good agent than the approval rate suggests.

This module records *realized outcomes* — what actually happened to a
candidate after the agent's decision was approved. Hooks into pipeline
state transitions:

- Application stage moves to ``technical_interview`` after an approved
  ``advance_to_interview`` agent decision → outcome="interviewed"
- Application outcome moves to ``hired`` after an approved
  ``advance_to_interview`` agent decision → outcome="hired"
- Application outcome moves to ``rejected`` after an approved reject
  decision → outcome="rejected_confirmed"

The recorded outcomes flow into TWO places:

  1. (canonical, per spec §3) ``HiringOutcome`` episodes in Graphiti,
     emitted by ``_emit_outcome_episode_safe`` below. Bi-temporal,
     auditable, the substrate every other stage reads from.

  2. (DEPRECATED — sunset when Graphiti is the only consumer)
     ``role.agent_calibration["outcomes"]`` bounded FIFO list,
     surfaced in the next cycle's system prompt via
     ``calibration_mod.render_summary``. Replaced by Graphiti outcome
     queries the system_prompt builder will read once the cycle's
     prompt-builder is migrated. Sunset target: when the system
     prompt's "track record" line is sourced from Graphiti outcome
     aggregates rather than this JSON FIFO.

No schema migration for the JSON path: stored entirely in the existing
``role.agent_calibration`` JSON column.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from . import calibration as calibration_mod


logger = logging.getLogger("taali.agent_runtime.outcome_learning")


def _latest_approved_decision(
    db: Session,
    *,
    application_id: int,
    decision_types: tuple[str, ...],
) -> Optional[AgentDecision]:
    """Find the most recent approved AgentDecision of the given types
    on this application. Returns None if none exists — most pipeline
    transitions are recruiter-driven, not agent-recommended, so this
    is a frequent miss and not an error."""
    return (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application_id,
            AgentDecision.status == "approved",
            AgentDecision.decision_type.in_(decision_types),
        )
        .order_by(AgentDecision.resolved_at.desc())
        .first()
    )


def _append_outcome(
    db: Session,
    *,
    role: Role,
    decision: AgentDecision,
    outcome: str,
    application_id: int,
) -> None:
    """Append one outcome entry to role.agent_calibration["outcomes"]."""
    now = datetime.now(timezone.utc)
    entry = {
        "decision_type": str(decision.decision_type),
        "decision_id": int(decision.id),
        "outcome": str(outcome),
        "observed_at": now.isoformat(),
        "application_id": int(application_id),
    }
    calibration_mod.save(db, role=role, updates={"outcomes": [entry]})

    # Phase 2 §6.7: emit a HiringOutcome episode (irreplaceable training
    # signal, low volume — one per realised outcome).
    _emit_outcome_episode_safe(
        db, decision=decision, application_id=application_id, outcome=outcome, observed_at=now,
    )


def _emit_outcome_episode_safe(
    db: Session,
    *,
    decision: AgentDecision,
    application_id: int,
    outcome: str,
    observed_at: datetime,
) -> None:
    """Best-effort HiringOutcome episode emit. Never raises.

    Maps the v1 outcome vocabulary
    (``hired`` / ``interviewed`` / ``rejected_confirmed``) to the v2
    outcome_type values defined in
    ``app.agent_runtime.contracts.HiringOutcome``.
    """
    try:
        from ..candidate_graph import agent_episodes
        from ..models.candidate import Candidate
        outcome_type_map = {
            "hired": "hired",
            "interviewed": "reached_interview",
            "rejected_confirmed": "rejected_late",
        }
        outcome_type = outcome_type_map.get(outcome, outcome)
        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .one_or_none()
        )
        if app is None:
            return
        candidate = (
            db.query(Candidate).filter(Candidate.id == app.candidate_id).one_or_none()
        )
        full_name = candidate.full_name if candidate is not None else None
        candidate_id = (
            int(candidate.id) if candidate is not None else int(app.candidate_id)
        )
        agent_episodes.emit_hiring_outcome_event(
            organization_id=int(decision.organization_id),
            candidate_full_name=full_name,
            candidate_taali_id=candidate_id,
            decision_id=int(decision.id),
            outcome_type=outcome_type,
            quality_signal=None,
            observed_at=observed_at,
        )
    except Exception:
        logger.warning(
            "outcome episode emit failed for decision_id=%s outcome=%s",
            getattr(decision, "id", None),
            outcome,
        )


def record_advance_outcome_on_stage(
    db: Session,
    *,
    application: CandidateApplication,
    new_stage: str,
) -> None:
    """Called from pipeline_service.transition_stage. When an application
    reaches ``technical_interview``, look up any approved agent advance
    decision and record outcome="interviewed".

    Idempotent — re-firing on the same stage transition just appends a
    duplicate entry, which the bounded FIFO eventually drops. Cheap
    enough not to bother deduping at insert time.
    """
    if str(new_stage) != "technical_interview":
        return
    role_id = getattr(application, "role_id", None)
    if role_id is None:
        return
    role = db.query(Role).filter(Role.id == int(role_id)).first()
    if role is None:
        return
    decision = _latest_approved_decision(
        db,
        application_id=int(application.id),
        decision_types=("advance_to_interview",),
    )
    if decision is None:
        return
    try:
        _append_outcome(
            db,
            role=role,
            decision=decision,
            outcome="interviewed",
            application_id=int(application.id),
        )
    except Exception:  # pragma: no cover — calibration is best-effort
        logger.exception(
            "outcome_learning: failed to record interviewed outcome "
            "(application_id=%s, decision_id=%s)",
            application.id, decision.id,
        )


def record_outcome_on_outcome_change(
    db: Session,
    *,
    application: CandidateApplication,
    new_outcome: str,
) -> None:
    """Called from pipeline_service.transition_outcome. When an
    application reaches ``hired`` or ``rejected``, record the realized
    outcome on the matching agent decision (if any).

    - hired after approved advance → outcome="hired"
    - rejected after approved reject / skip_assessment_reject →
      outcome="rejected_confirmed"
    """
    role_id = getattr(application, "role_id", None)
    if role_id is None:
        return
    role = db.query(Role).filter(Role.id == int(role_id)).first()
    if role is None:
        return

    target = str(new_outcome)
    if target == "hired":
        decision = _latest_approved_decision(
            db,
            application_id=int(application.id),
            decision_types=("advance_to_interview",),
        )
        recorded = "hired"
    elif target == "rejected":
        decision = _latest_approved_decision(
            db,
            application_id=int(application.id),
            decision_types=("reject", "skip_assessment_reject"),
        )
        recorded = "rejected_confirmed"
    else:
        return

    if decision is None:
        return
    try:
        _append_outcome(
            db,
            role=role,
            decision=decision,
            outcome=recorded,
            application_id=int(application.id),
        )
    except Exception:  # pragma: no cover — calibration is best-effort
        logger.exception(
            "outcome_learning: failed to record %s outcome "
            "(application_id=%s, decision_id=%s)",
            recorded, application.id, decision.id,
        )


__all__ = [
    "record_advance_outcome_on_stage",
    "record_outcome_on_outcome_change",
]
