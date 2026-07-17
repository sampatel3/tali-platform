"""Durable outbox for Graphiti episode writes.

The ``emit_*`` helpers in ``agent_episodes`` are fire-and-forget: they
swallow every error and return a bool. That's fine for episodes that can
be re-derived from Postgres (scores, decisions), but it silently drops
``HiringOutcome`` episodes — the realised-outcome training signal that
*cannot* be reconstructed once lost.

This module is the durable hop:

  ``enqueue_*`` writes a ``graph_episode_outbox`` row in the caller's
  transaction (no graph client involved, so it lands even when Graphiti is
  down or unconfigured). ``drain`` later rebuilds each pending episode and
  dispatches it to Graphiti; a send that doesn't land leaves the row
  ``pending`` with bounded retry cooldown instead of vanishing. Provider,
  budget, and metering outages are never made terminal by attempt count.

Rebuilding (rather than storing the rendered ``Episode``) keeps the
episode-body templates in one place — ``agent_episodes`` — so a template
change applies to drained rows too.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.graph_episode_outbox import (
    EPISODE_KIND_DECISION,
    EPISODE_KIND_HIRING_OUTCOME,
    OUTBOX_STATUS_PENDING,
    GraphEpisodeOutbox,
)
from . import agent_episodes
from .episodes import Episode


logger = logging.getLogger("taali.candidate_graph.episode_outbox")


# Kept as a compatibility default for callers/tests that still pass
# ``max_attempts``.  Transient failures are no longer terminal at this number:
# an irreplaceable episode must recover after an arbitrarily long provider,
# budget, or graph outage.  Only structurally invalid payloads become failed.
_MAX_ATTEMPTS = 8
_DRAIN_BATCH_SIZE = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enqueue — pure DB inserts, atomic with the caller's transaction.
# ---------------------------------------------------------------------------


def _enqueue(
    db: Session,
    *,
    organization_id: int,
    episode_kind: str,
    dedup_key: str,
    payload: dict[str, Any],
) -> Optional[GraphEpisodeOutbox]:
    """Insert one pending outbox row. Idempotent on ``dedup_key``.

    Returns the row (existing or newly created), or None when there's no
    org context to namespace the episode. Never contacts Graphiti — the
    whole point is that this survives a graph outage.
    """
    if int(organization_id) <= 0:
        return None
    existing = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.dedup_key == dedup_key)
        .one_or_none()
    )
    if existing is not None:
        return existing
    row = GraphEpisodeOutbox(
        organization_id=int(organization_id),
        episode_kind=episode_kind,
        dedup_key=dedup_key,
        payload=payload,
        status=OUTBOX_STATUS_PENDING,
        attempts=0,
    )
    db.add(row)
    db.flush()
    return row


def enqueue_hiring_outcome(
    db: Session,
    *,
    organization_id: int,
    candidate_full_name: str | None,
    candidate_taali_id: int,
    decision_id: int,
    role_id: int,
    outcome_type: str,
    quality_signal: float | None,
    observed_at: datetime,
) -> Optional[GraphEpisodeOutbox]:
    """Durably enqueue a ``HiringOutcome`` episode for later dispatch."""
    payload = {
        "organization_id": int(organization_id),
        "candidate_full_name": candidate_full_name,
        "candidate_taali_id": int(candidate_taali_id),
        "decision_id": int(decision_id),
        "role_id": int(role_id),
        "outcome_type": str(outcome_type),
        "quality_signal": quality_signal,
        "observed_at": observed_at.isoformat(),
    }
    return _enqueue(
        db,
        organization_id=int(organization_id),
        episode_kind=EPISODE_KIND_HIRING_OUTCOME,
        dedup_key=f"hiring-outcome-{int(decision_id)}-{outcome_type}",
        payload=payload,
    )


def enqueue_decision(
    db: Session,
    *,
    organization_id: int,
    candidate_full_name: str | None,
    candidate_taali_id: int,
    application_id: int,
    role_id: int,
    decision_id: int,
    recommended_action: str,
    confidence: float,
    policy_revision_id: int | None,
    reasoning: str,
    created_at: datetime,
    features_json: dict[str, Any] | None = None,
) -> Optional[GraphEpisodeOutbox]:
    """Durably enqueue a ``DecisionEvent`` episode for later dispatch."""
    payload = {
        "organization_id": int(organization_id),
        "candidate_full_name": candidate_full_name,
        "candidate_taali_id": int(candidate_taali_id),
        "application_id": int(application_id),
        "role_id": int(role_id),
        "decision_id": int(decision_id),
        "recommended_action": str(recommended_action),
        "confidence": float(confidence),
        "policy_revision_id": policy_revision_id,
        "reasoning": reasoning,
        "created_at": created_at.isoformat(),
        "features_json": features_json,
    }
    return _enqueue(
        db,
        organization_id=int(organization_id),
        episode_kind=EPISODE_KIND_DECISION,
        dedup_key=f"agent-decision-{int(decision_id)}",
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Drain — rebuild + dispatch pending rows to Graphiti.
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return _now()


def _build_episode(row: GraphEpisodeOutbox) -> Episode | None:
    """Reconstruct the Graphiti episode for a row from its stored payload."""
    payload = dict(row.payload or {})
    if row.episode_kind == EPISODE_KIND_HIRING_OUTCOME:
        return agent_episodes.build_hiring_outcome_episode(
            organization_id=int(payload["organization_id"]),
            candidate_full_name=payload.get("candidate_full_name"),
            candidate_taali_id=int(payload["candidate_taali_id"]),
            decision_id=int(payload["decision_id"]),
            outcome_type=str(payload["outcome_type"]),
            quality_signal=payload.get("quality_signal"),
            observed_at=_parse_dt(payload.get("observed_at")),
        )
    if row.episode_kind == EPISODE_KIND_DECISION:
        return agent_episodes.build_decision_episode(
            organization_id=int(payload["organization_id"]),
            candidate_full_name=payload.get("candidate_full_name"),
            candidate_taali_id=int(payload["candidate_taali_id"]),
            application_id=int(payload["application_id"]),
            role_id=int(payload["role_id"]),
            decision_id=int(payload["decision_id"]),
            recommended_action=str(payload["recommended_action"]),
            confidence=float(payload.get("confidence") or 0.0),
            policy_revision_id=payload.get("policy_revision_id"),
            reasoning=str(payload.get("reasoning") or ""),
            created_at=_parse_dt(payload.get("created_at")),
            features_json=payload.get("features_json"),
        )
    logger.warning(
        "graph_episode_outbox: unknown episode_kind=%s (row id=%s)",
        row.episode_kind,
        row.id,
    )
    return None


def drain(
    db: Session,
    *,
    batch_size: int = _DRAIN_BATCH_SIZE,
    max_attempts: int = _MAX_ATTEMPTS,
) -> dict:
    """Send pending rows without retaining DB locks across Graphiti calls."""
    # Kept for callers that still pass the former cap. Irreplaceable graph
    # signals remain retryable; only structurally invalid rows are terminal.
    _ = max_attempts
    from .episode_outbox_delivery import drain as drain_deliveries

    return drain_deliveries(
        db,
        batch_size=int(batch_size),
        build_episode=_build_episode,
    )


__all__ = [
    "enqueue_hiring_outcome",
    "enqueue_decision",
    "drain",
]
