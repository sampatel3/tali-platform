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
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.graph_episode_outbox import (
    EPISODE_KIND_DECISION,
    EPISODE_KIND_HIRING_OUTCOME,
    EPISODE_KIND_RECRUITER_ACTION,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SENT,
    GraphEpisodeOutbox,
)
from ..models.organization import Organization
from ..models.role import Role
from . import agent_episodes
from . import client as graph_client
from . import episode_outbox_query
from . import episodes as episode_module
from .episodes import Episode


logger = logging.getLogger("taali.candidate_graph.episode_outbox")


_DRAIN_BATCH_SIZE = 200
_MAX_ROLE_ID = 2_147_483_647
_MAX_DECISION_ID = 9_223_372_036_854_775_807


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enqueue — pure DB inserts, atomic with the caller's transaction.
# ---------------------------------------------------------------------------


def _enqueue(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
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
        role_id=int(role_id),
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
        role_id=int(role_id),
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
        role_id=int(role_id),
        episode_kind=EPISODE_KIND_DECISION,
        dedup_key=f"agent-decision-{int(decision_id)}",
        payload=payload,
    )


def enqueue_recruiter_action(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    decision_id: int,
    recruiter_id: int,
    action: str,
    reason: str | None,
    happened_at: datetime,
) -> Optional[GraphEpisodeOutbox]:
    """Durably enqueue a recruiter approval/override graph episode."""
    payload = {
        "organization_id": int(organization_id),
        "role_id": int(role_id),
        "decision_id": int(decision_id),
        "recruiter_id": int(recruiter_id),
        "action": str(action),
        "reason": reason,
        "happened_at": happened_at.isoformat(),
    }
    return _enqueue(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
        episode_kind=EPISODE_KIND_RECRUITER_ACTION,
        dedup_key=f"recruiter-action-{str(action)}-{int(decision_id)}",
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Drain — rebuild + dispatch pending rows to Graphiti.
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid episode timestamp") from exc
    else:
        raise ValueError("missing episode timestamp")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_positive_int(value: Any, *, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= maximum else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.isascii() or not stripped.isdecimal():
            return None
        significant = stripped.lstrip("0")
        if not significant:
            return None
        maximum_text = str(maximum)
        if len(significant) > len(maximum_text) or (
            len(significant) == len(maximum_text) and significant > maximum_text
        ):
            return None
        return int(significant)
    return None


def _required_positive_int(
    payload: dict[str, Any],
    key: str,
    *,
    maximum: int,
) -> int:
    value = _bounded_positive_int(payload.get(key), maximum=maximum)
    if value is None:
        raise ValueError(f"invalid {key}")
    return value


def _required_nonnegative_int(
    payload: dict[str, Any],
    key: str,
    *,
    maximum: int,
) -> int:
    raw_value = payload.get(key)
    if isinstance(raw_value, int) and not isinstance(raw_value, bool) and raw_value == 0:
        return 0
    return _required_positive_int(payload, key, maximum=maximum)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid {key}")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid {key}")
    return value


def _optional_finite_float(payload: dict[str, Any], key: str) -> float | None:
    raw_value = payload.get(key)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        raise ValueError(f"invalid {key}")
    value = float(raw_value)
    if not math.isfinite(value):
        raise ValueError(f"invalid {key}")
    return value


def _required_finite_float(payload: dict[str, Any], key: str) -> float:
    value = _optional_finite_float(payload, key)
    if value is None:
        raise ValueError(f"invalid {key}")
    return value


def _build_episode(
    row: GraphEpisodeOutbox,
    *,
    payload: dict[str, Any],
    role_id: int,
) -> Episode | None:
    """Reconstruct the Graphiti episode for a row from its stored payload."""
    row_organization_id = _bounded_positive_int(
        row.organization_id,
        maximum=_MAX_ROLE_ID,
    )
    payload_organization_id = _required_positive_int(
        payload,
        "organization_id",
        maximum=_MAX_ROLE_ID,
    )
    if row_organization_id is None or payload_organization_id != row_organization_id:
        raise ValueError("payload organization_id does not match outbox row")

    raw_payload_role_id = payload.get("role_id")
    if raw_payload_role_id is not None:
        payload_role_id = _bounded_positive_int(
            raw_payload_role_id,
            maximum=_MAX_ROLE_ID,
        )
        if payload_role_id is None or payload_role_id != int(role_id):
            raise ValueError("payload role_id does not match outbox row")

    if row.episode_kind == EPISODE_KIND_HIRING_OUTCOME:
        return agent_episodes.build_hiring_outcome_episode(
            organization_id=row_organization_id,
            candidate_full_name=_optional_string(payload, "candidate_full_name"),
            candidate_taali_id=_required_positive_int(
                payload, "candidate_taali_id", maximum=_MAX_ROLE_ID
            ),
            decision_id=_required_positive_int(
                payload, "decision_id", maximum=_MAX_DECISION_ID
            ),
            outcome_type=_required_string(payload, "outcome_type"),
            quality_signal=_optional_finite_float(payload, "quality_signal"),
            observed_at=_parse_dt(payload.get("observed_at")),
            role_id=int(role_id),
        )
    if row.episode_kind == EPISODE_KIND_DECISION:
        raw_features = payload.get("features_json")
        if raw_features is not None and not isinstance(raw_features, dict):
            raise ValueError("invalid features_json")
        raw_policy_revision_id = payload.get("policy_revision_id")
        policy_revision_id = None
        if raw_policy_revision_id is not None:
            policy_revision_id = _required_positive_int(
                payload,
                "policy_revision_id",
                maximum=_MAX_DECISION_ID,
            )
        return agent_episodes.build_decision_episode(
            organization_id=row_organization_id,
            candidate_full_name=_optional_string(payload, "candidate_full_name"),
            candidate_taali_id=_required_positive_int(
                payload, "candidate_taali_id", maximum=_MAX_ROLE_ID
            ),
            application_id=_required_positive_int(
                payload, "application_id", maximum=_MAX_ROLE_ID
            ),
            role_id=int(role_id),
            decision_id=_required_positive_int(
                payload, "decision_id", maximum=_MAX_DECISION_ID
            ),
            recommended_action=_required_string(payload, "recommended_action"),
            confidence=_required_finite_float(payload, "confidence"),
            policy_revision_id=policy_revision_id,
            reasoning=_optional_string(payload, "reasoning") or "",
            created_at=_parse_dt(payload.get("created_at")),
            features_json=raw_features,
        )
    if row.episode_kind == EPISODE_KIND_RECRUITER_ACTION:
        return agent_episodes.build_recruiter_action_episode(
            organization_id=row_organization_id,
            role_id=int(role_id),
            decision_id=_required_positive_int(
                payload, "decision_id", maximum=_MAX_DECISION_ID
            ),
            recruiter_id=_required_nonnegative_int(
                payload, "recruiter_id", maximum=_MAX_ROLE_ID
            ),
            action=_required_string(payload, "action"),
            reason=_optional_string(payload, "reason"),
            happened_at=_parse_dt(payload.get("happened_at")),
        )
    logger.warning(
        "graph_episode_outbox: unknown episode_kind=%s (row id=%s)",
        row.episode_kind,
        row.id,
    )
    return None


def _retry_delay(attempts: int) -> timedelta:
    """Bounded exponential cooldown between durable dispatch attempts."""
    return timedelta(
        seconds=episode_outbox_query.retry_delay_seconds(int(attempts))
    )


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retry_is_due(row: GraphEpisodeOutbox, *, now: datetime) -> bool:
    attempts = int(row.attempts or 0)
    if attempts <= 0:
        return True
    updated_at = _as_aware_utc(row.updated_at)
    if updated_at is None:
        return True
    return updated_at + _retry_delay(attempts) <= now


def _candidate_role_id(
    db: Session,
    row: GraphEpisodeOutbox,
    *,
    payload: dict[str, Any],
) -> int | None:
    """Resolve the candidate role that owns this episode's provider spend.

    New rows persist ``role_id`` directly. Payload/decision fallbacks keep
    legacy rows and NULLs inserted by older rolling-deploy workers recoverable.
    The fresh tri-state authority query validates this ID immediately before
    dispatch and only then repairs legacy ownership.
    """
    role_id = _bounded_positive_int(row.role_id, maximum=_MAX_ROLE_ID)
    if row.role_id is not None and role_id is None:
        return None

    if role_id is None:
        raw_role_id = payload.get("role_id")
        if raw_role_id is not None:
            role_id = _bounded_positive_int(
                raw_role_id,
                maximum=_MAX_ROLE_ID,
            )
            if role_id is None:
                return None
        else:
            decision_id = _bounded_positive_int(
                payload.get("decision_id"),
                maximum=_MAX_DECISION_ID,
            )
            if decision_id is None:
                return None
            role_id = (
                db.query(AgentDecision.role_id)
                .filter(
                    AgentDecision.id == decision_id,
                    AgentDecision.organization_id == int(row.organization_id),
                )
                .scalar()
            )
            if role_id is None:
                return None
            role_id = int(role_id)

    return int(role_id)


def _role_dispatch_state(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
) -> bool | None:
    """Return ``None`` for invalid ownership, otherwise current authority.

    Rows remain durable while a role is paused or off, but the five-minute
    drain must not turn that backlog into new model/embedding spend. The row is
    simply reconsidered on a later tick after the recruiter resumes the role.
    """
    state = (
        db.query(
            Role.agentic_mode_enabled,
            Role.agent_paused_at,
            Organization.agent_workspace_paused_at,
        )
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if state is None:
        return None
    return bool(
        state.agentic_mode_enabled
        and state.agent_paused_at is None
        and state.agent_workspace_paused_at is None
    )


def drain(
    db: Session,
    *,
    batch_size: int = _DRAIN_BATCH_SIZE,
) -> dict:
    """Send pending outbox rows to Graphiti. Idempotent + retry-safe.

    Returns a summary dict. Behaviour:
    - Graphiti not configured → no-op (rows untouched, picked up next drain).
      We never burn an attempt on a legitimately-unconfigured graph.
    - send lands (dispatch >= 1) → row marked ``sent``.
    - send doesn't land (provider/budget/metering/graph failure) → attempts is
      incremented and the row stays ``pending`` indefinitely with a bounded
      exponential cooldown. It recovers automatically when the dependency or
      budget does, even if that takes days.
    - episode kinds introduced by a newer deployment remain untouched so the
      newer worker can drain them during a mixed-version rollout.
    - only a structurally invalid row (unbuildable payload or no valid billing
      role) becomes terminal ``failed``.
    """
    if not graph_client.is_configured():
        return {"status": "unconfigured", "scanned": 0, "sent": 0, "failed": 0}

    drain_now = _now()
    # Claim only due, actionable/repairable rows. Normalized held and cooling
    # rows remain durable without being repeatedly locked and decoded.
    locked_rows = episode_outbox_query.lock_pending_outbox_rows(
        db,
        now=drain_now,
        batch_size=int(batch_size),
    )
    rows = [row for row in locked_rows if _retry_is_due(row, now=drain_now)]
    deferred = len(locked_rows) - len(rows)
    role_deferred = 0

    sent = 0
    failed = 0
    still_pending = 0
    for row in rows:
        now = _now()
        payload = row.payload if isinstance(row.payload, dict) else None
        if payload is None:
            row.status = OUTBOX_STATUS_FAILED
            row.last_error = "invalid episode payload: expected JSON object"
            row.updated_at = now
            failed += 1
            continue

        role_id = _candidate_role_id(db, row, payload=payload)
        if role_id is None:
            # Automatic provider spend is never allowed to fall back to an
            # org-only/unattributed call.  Missing or cross-org role ownership
            # is a payload integrity defect, not a transient provider outage.
            row.status = OUTBOX_STATUS_FAILED
            row.last_error = "valid role attribution unavailable for graph billing"
            row.updated_at = now
            failed += 1
            continue
        try:
            episode = _build_episode(row, payload=payload, role_id=role_id)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            episode = None
            invalid_reason = f"invalid episode payload: {exc}"
        else:
            invalid_reason = "episode could not be rebuilt from payload"
        if episode is None:
            # Unbuildable rows will never succeed — don't retry forever.
            row.status = OUTBOX_STATUS_FAILED
            row.last_error = invalid_reason
            row.updated_at = now
            failed += 1
            continue
        role_dispatch_state = _role_dispatch_state(
            db,
            organization_id=int(row.organization_id),
            role_id=int(role_id),
        )
        if role_dispatch_state is None:
            row.status = OUTBOX_STATUS_FAILED
            row.last_error = "valid role attribution unavailable for graph billing"
            row.updated_at = now
            failed += 1
            continue
        if row.role_id is None:
            row.role_id = int(role_id)
        if not role_dispatch_state:
            # Pause/Turn off is a temporary execution hold, not corruption and
            # not a provider failure. Keep the durable signal pending without
            # consuming an attempt; a later drain resumes it automatically.
            deferred += 1
            role_deferred += 1
            continue
        try:
            # Attribute the spend: the row always carries organization_id,
            # and the payload carries role/candidate for DECISION episodes.
            # The billing context makes the metered async wrapper write a
            # per-org usage_event (feature=graph_sync) for each provider call,
            # so outbox-drained indexing flows into the organization's budget.
            _cand_id = _bounded_positive_int(
                payload.get("candidate_taali_id"),
                maximum=_MAX_ROLE_ID,
            )
            _recruiter_id = _bounded_positive_int(
                payload.get("recruiter_id"),
                maximum=_MAX_ROLE_ID,
            )
            n = episode_module.dispatch(
                [episode],
                bill_organization_id=int(row.organization_id),
                bill_role_id=int(role_id),
                bill_user_id=_recruiter_id,
                bill_candidate_id=_cand_id,
                bill_trace_id=f"graph-outbox:{int(row.id)}:{row.dedup_key}",
                require_hard_admission=True,
                require_role_admission=True,
                raise_on_error=True,
            )
            err: str | None = None
        except Exception as exc:
            n = 0
            err = str(exc)

        if n > 0:
            row.status = OUTBOX_STATUS_SENT
            row.sent_at = now
            row.updated_at = now
            row.last_error = None
            sent += 1
        else:
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = err or "graph dispatch returned 0 (send did not land)"
            row.updated_at = now
            row.status = OUTBOX_STATUS_PENDING
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
        "deferred": deferred,
        "role_deferred": role_deferred,
    }


__all__ = [
    "enqueue_hiring_outcome",
    "enqueue_decision",
    "enqueue_recruiter_action",
    "drain",
]
