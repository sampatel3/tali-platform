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

- Downstream hiring stage reaches ``interviewing`` after an approved
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


# Only POSITIVE / advance outcomes are projected into Graphiti. Every graph
# prior query (candidate_graph.graphrag_queries) keys on outcome_type='hired'
# as the numerator and counts the *candidate* population (Candidate nodes,
# always synced) as the denominator — so a rejected / withdrawn candidate is
# already represented as "a candidate with no positive outcome". Their
# negative signal is free (inferred by absence); materialising a negative
# HiringOutcome episode buys nothing the priors read while costing ~30
# Graphiti entity/edge dedup calls per episode — the dominant graph_sync
# spend (rejected_late was 95% of it on 2026-06-07). Negatives still land in
# the Postgres calibration FIFO + agent_decisions (the source of truth for
# policy learning); only the graph projection is skipped.
_GRAPH_WORTHY_OUTCOME_TYPES = frozenset(
    {"hired", "received_offer", "reached_interview"}
)


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
    existing_outcomes = (role.agent_calibration or {}).get("outcomes") or []
    if any(
        isinstance(item, dict)
        and int(item.get("decision_id") or 0) == int(decision.id)
        and str(item.get("outcome") or "") == str(outcome)
        for item in existing_outcomes
    ):
        return
    now = datetime.now(timezone.utc)
    entry = {
        "decision_type": str(decision.decision_type),
        "decision_id": int(decision.id),
        "outcome": str(outcome),
        "observed_at": now.isoformat(),
        "application_id": int(application_id),
    }
    calibration_mod.save(db, role=role, updates={"outcomes": [entry]})

    # Phase 2 §6.7: durably enqueue a HiringOutcome episode. This is the
    # irreplaceable training signal (one per realised outcome) — what
    # actually happened to a candidate after an approved decision, which
    # cannot be re-derived months later. The old path emitted to Graphiti
    # fire-and-forget, so a graph outage silently dropped it. Now we write
    # to a local outbox in THIS transaction (no graph call here, so it
    # lands even when Graphiti is down/unconfigured) and a Celery drain
    # task ships it to Graphiti with retry. See candidate_graph.episode_outbox.
    _enqueue_outcome_episode(
        db, decision=decision, application_id=application_id, outcome=outcome, observed_at=now,
    )


def _enqueue_outcome_episode(
    db: Session,
    *,
    decision: AgentDecision,
    application_id: int,
    outcome: str,
    observed_at: datetime,
) -> None:
    """Enqueue a HiringOutcome episode into the durable graph outbox.

    Maps the v1 outcome vocabulary
    (``hired`` / ``interviewed`` / ``rejected_confirmed``) to the v2
    outcome_type values defined in
    ``app.agent_runtime.contracts.HiringOutcome``, then writes a
    ``graph_episode_outbox`` row. Does NOT contact Graphiti — the drain
    task does — so the signal survives a graph outage. Participates in the
    caller's transaction (the calibration write and this enqueue commit or
    roll back together); callers wrap it best-effort.
    """
    from ..candidate_graph import episode_outbox
    from ..models.candidate import Candidate

    outcome_type_map = {
        "hired": "hired",
        "interviewed": "reached_interview",
        "rejected_confirmed": "rejected_late",
    }
    outcome_type = outcome_type_map.get(outcome, outcome)
    # Cost gate (2026-06-07): only project positive/advance outcomes into the
    # graph. Rejects/withdrawals are inferred by absence among the candidate
    # population the priors already count — see _GRAPH_WORTHY_OUTCOME_TYPES.
    if outcome_type not in _GRAPH_WORTHY_OUTCOME_TYPES:
        return
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
    episode_outbox.enqueue_hiring_outcome(
        db,
        organization_id=int(decision.organization_id),
        candidate_full_name=full_name,
        candidate_taali_id=candidate_id,
        decision_id=int(decision.id),
        role_id=int(decision.role_id),
        outcome_type=outcome_type,
        quality_signal=None,
        observed_at=observed_at,
    )


def record_advance_outcome_on_stage(
    db: Session,
    *,
    application: CandidateApplication,
    new_stage: str,
) -> None:
    """Compatibility hook called from pipeline-stage transitions.

    ``advanced`` now means evaluation handoff, not proof an interview happened,
    so this function deliberately records nothing. Interview learning is fired
    by :func:`record_interview_outcome_on_recruiter_stage` when the independent
    hiring-stage axis actually reaches ``interviewing``.

    Idempotent — re-firing on the same stage transition just appends a
    duplicate entry, which the bounded FIFO eventually drops. Cheap
    enough not to bother deduping at insert time.
    """
    return


def record_interview_outcome_on_recruiter_stage(
    db: Session,
    *,
    application: CandidateApplication,
    new_stage: str,
) -> None:
    """Record reached-interview evidence, including later monotonic stages."""
    if str(new_stage) not in {"interviewing", "offer", "hired"}:
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


def record_outcome_for_approved_decision(
    db: Session,
    *,
    decision: AgentDecision,
    application: CandidateApplication,
) -> None:
    """Record the realised outcome at the moment a decision is approved.

    The transition hooks above look the decision up by ``status="approved"``
    and exist for genuinely-later downstream transitions (e.g. a hire weeks
    after an advance). They miss the agent's own approve action, because
    Approving a reject sets ``application_outcome="rejected"`` before the
    decision is stamped ``approved``, so that result is recorded here with the
    decision in hand. An approved advance is only an evaluation handoff unless
    an external ATS already observed interviewing/offer/hired before approval;
    reconcile that known milestone here.
    """
    role_id = getattr(application, "role_id", None)
    if role_id is None:
        return
    dtype = str(decision.decision_type)
    if (
        dtype in ("reject", "skip_assessment_reject")
        and str(application.application_outcome) == "rejected"
    ):
        outcome = "rejected_confirmed"
    elif dtype == "advance_to_interview":
        from ..services.recruiter_stage_service import current_recruiter_stage

        if current_recruiter_stage(application) in {"interviewing", "offer", "hired"}:
            outcome = "interviewed"
        else:
            return
    else:
        return
    role = db.query(Role).filter(Role.id == int(role_id)).first()
    if role is None:
        return
    _append_outcome(
        db,
        role=role,
        decision=decision,
        outcome=outcome,
        application_id=int(application.id),
    )


__all__ = [
    "record_advance_outcome_on_stage",
    "record_interview_outcome_on_recruiter_stage",
    "record_outcome_on_outcome_change",
    "record_outcome_for_approved_decision",
]
