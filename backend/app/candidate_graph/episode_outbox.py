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
  ``pending`` (until a retry cap) instead of vanishing.

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
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SENT,
    GraphEpisodeOutbox,
)
from . import agent_episodes
from . import client as graph_client
from . import episodes as episode_module
from .episodes import Episode


logger = logging.getLogger("taali.candidate_graph.episode_outbox")


# Bounded retry budget. The drain runs on a beat schedule (see
# ``celery_app``); ~8 attempts over hours/days comfortably outlasts a
# transient Graphiti / Neo4j / Voyage outage. After the cap the row is
# marked ``failed`` and surfaced in logs rather than retried forever.
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
    """Send pending outbox rows to Graphiti. Idempotent + retry-safe.

    Returns a summary dict. Behaviour:
    - Graphiti not configured → no-op (rows untouched, picked up next drain).
      We never burn an attempt on a legitimately-unconfigured graph.
    - send lands (dispatch >= 1) → row marked ``sent``.
    - send doesn't land (dispatch == 0 or raised) → ``attempts`` is
      incremented and the row stays ``pending`` until the cap, then
      ``failed``. A still-``pending`` row is retried on the next drain — the
      irreplaceable signal is never dropped.
    - row can't be rebuilt (unknown kind / missing org) → terminal ``failed``.
    """
    if not graph_client.is_configured():
        return {"status": "unconfigured", "scanned": 0, "sent": 0, "failed": 0}

    rows = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.status == OUTBOX_STATUS_PENDING)
        .order_by(GraphEpisodeOutbox.id.asc())
        .limit(int(batch_size))
        .all()
    )

    sent = 0
    failed = 0
    still_pending = 0
    for row in rows:
        episode = _build_episode(row)
        now = _now()
        if episode is None:
            # Unbuildable rows will never succeed — don't retry forever.
            row.status = OUTBOX_STATUS_FAILED
            row.last_error = "episode could not be rebuilt from payload"
            row.updated_at = now
            failed += 1
            continue
        try:
            # Attribute the spend: the row always carries organization_id,
            # and the payload carries role/candidate for DECISION episodes.
            # Passing db + bill_* makes the metered async wrapper write a
            # per-org usage_event (feature=graph_sync) for each Anthropic
            # call, so outbox-drained indexing flows into the org's budget
            # instead of landing as an unattributed (org=NULL) call_log row.
            payload = dict(row.payload or {})
            _role_id = payload.get("role_id")
            _cand_id = payload.get("candidate_taali_id")
            n = episode_module.dispatch(
                [episode],
                db=db,
                bill_organization_id=int(row.organization_id),
                bill_role_id=int(_role_id) if _role_id is not None else None,
                bill_candidate_id=int(_cand_id) if _cand_id is not None else None,
            )
            err: str | None = None
        except Exception as exc:  # dispatch swallows per-episode errors, but be safe
            n = 0
            err = str(exc)

        if n > 0:
            row.status = OUTBOX_STATUS_SENT
            row.sent_at = now
            row.updated_at = now
            sent += 1
        else:
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = err or "graph dispatch returned 0 (send did not land)"
            row.updated_at = now
            if row.attempts >= int(max_attempts):
                row.status = OUTBOX_STATUS_FAILED
                failed += 1
            else:
                still_pending += 1

    db.commit()
    if failed:
        logger.warning(
            "graph_episode_outbox drain: scanned=%d sent=%d failed=%d pending=%d",
            len(rows), sent, failed, still_pending,
        )
    return {
        "status": "ok",
        "scanned": len(rows),
        "sent": sent,
        "failed": failed,
        "pending": still_pending,
    }


__all__ = [
    "enqueue_hiring_outcome",
    "enqueue_decision",
    "drain",
]
