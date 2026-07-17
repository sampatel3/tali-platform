"""Short-transaction delivery lifecycle for durable graph episodes."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.graph_episode_outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SENT,
    GraphEpisodeOutbox,
)
from ..models.organization import Organization
from ..models.role import Role
from . import client as graph_client
from . import episodes as episode_module
from .episodes import Episode

logger = logging.getLogger("taali.candidate_graph.episode_outbox")

_RETRY_BASE_SECONDS = 300
_RETRY_MAX_SECONDS = 3_600
_INVALID_PAYLOAD_ERROR = "invalid_episode_payload"
_REBUILD_ERROR = "episode_rebuild_failed"
_ROLE_ATTRIBUTION_ERROR = "graph_role_attribution_invalid"
_DISPATCH_ERROR = "graph_dispatch_failed"
_NO_ACK_ERROR = "graph_dispatch_no_ack"
_STATE_DRIFT_ERROR = "graph_dispatch_state_drift"


@dataclass(frozen=True)
class DeliveryClaim:
    """Immutable primitive snapshot carried across the provider boundary."""

    row_id: int
    attempt: int
    organization_id: int
    role_id: int
    candidate_id: int | None
    dedup_key: str
    row_fingerprint: str
    episode: Episode = field(repr=False)


@dataclass(frozen=True)
class ClaimResult:
    row_id: int
    outcome: Literal["cooldown", "role_deferred", "failed", "delivery"]
    claim: DeliveryClaim | None = None


def _retry_delay(attempts: int) -> timedelta:
    exponent = max(min(int(attempts) - 1, 10), 0)
    seconds = min(_RETRY_MAX_SECONDS, _RETRY_BASE_SECONDS * (2**exponent))
    return timedelta(seconds=seconds)


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
    return updated_at is None or updated_at + _retry_delay(attempts) <= now


def _billing_role_id(db: Session, row: GraphEpisodeOutbox) -> int | None:
    """Resolve a current, same-org role for automatic provider admission."""
    payload = dict(row.payload or {})
    raw_role_id = payload.get("role_id")
    try:
        role_id = int(raw_role_id) if raw_role_id is not None else None
    except (TypeError, ValueError):
        return None
    if role_id is None:
        try:
            decision_id = int(payload["decision_id"])
        except (KeyError, TypeError, ValueError):
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
    valid = (
        db.query(Role.id)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(row.organization_id),
            Role.deleted_at.is_(None),
        )
        .scalar()
    )
    return int(valid) if valid is not None else None


def _role_allows_dispatch(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
) -> bool:
    return (
        db.query(Role.id)
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.is_(None),
            Organization.agent_workspace_paused_at.is_(None),
        )
        .scalar()
        is not None
    )


def _row_fingerprint(row: GraphEpisodeOutbox) -> str:
    canonical = json.dumps(
        {
            "organization_id": int(row.organization_id),
            "episode_kind": str(row.episode_kind),
            "dedup_key": str(row.dedup_key),
            "payload": dict(row.payload or {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_next(
    db: Session,
    *,
    excluded_ids: set[int],
    now: datetime,
    build_episode: Callable[[GraphEpisodeOutbox], Episode | None],
) -> ClaimResult | None:
    query = db.query(GraphEpisodeOutbox).filter(
        GraphEpisodeOutbox.status == OUTBOX_STATUS_PENDING
    )
    if excluded_ids:
        query = query.filter(GraphEpisodeOutbox.id.notin_(excluded_ids))
    row = (
        query.order_by(
            GraphEpisodeOutbox.updated_at.asc(), GraphEpisodeOutbox.id.asc()
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        db.rollback()
        return None
    row_id = int(row.id)
    if not _retry_is_due(row, now=now):
        db.rollback()
        return ClaimResult(row_id=row_id, outcome="cooldown")

    try:
        episode = build_episode(row)
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception(
            "graph episode payload invalid row_id=%s error_type=%s",
            row_id,
            type(exc).__name__,
        )
        episode = None
        invalid_reason = _INVALID_PAYLOAD_ERROR
    else:
        invalid_reason = _REBUILD_ERROR
    if episode is None:
        row.status = OUTBOX_STATUS_FAILED
        row.last_error = invalid_reason
        row.updated_at = now
        db.commit()
        return ClaimResult(row_id=row_id, outcome="failed")

    role_id = _billing_role_id(db, row)
    if role_id is None:
        row.status = OUTBOX_STATUS_FAILED
        row.last_error = _ROLE_ATTRIBUTION_ERROR
        row.updated_at = now
        db.commit()
        return ClaimResult(row_id=row_id, outcome="failed")
    if not _role_allows_dispatch(
        db,
        organization_id=int(row.organization_id),
        role_id=int(role_id),
    ):
        db.rollback()
        return ClaimResult(row_id=row_id, outcome="role_deferred")

    row.attempts = int(row.attempts or 0) + 1
    row.updated_at = now
    payload = dict(row.payload or {})
    raw_candidate_id = payload.get("candidate_taali_id")
    candidate_id = int(raw_candidate_id) if raw_candidate_id is not None else None
    claim = DeliveryClaim(
        row_id=row_id,
        attempt=int(row.attempts),
        organization_id=int(row.organization_id),
        role_id=int(role_id),
        candidate_id=candidate_id,
        dedup_key=str(row.dedup_key),
        row_fingerprint=_row_fingerprint(row),
        episode=episode,
    )
    db.commit()
    return ClaimResult(row_id=row_id, outcome="delivery", claim=claim)


def _finalize(
    db: Session,
    *,
    claim: DeliveryClaim,
    delivered: bool,
    error_code: str | None,
    now: datetime,
) -> str:
    row = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.id == int(claim.row_id))
        .with_for_update()
        .one_or_none()
    )
    if (
        row is None
        or row.status != OUTBOX_STATUS_PENDING
        or int(row.attempts or 0) != int(claim.attempt)
    ):
        db.rollback()
        return "stale"
    if _row_fingerprint(row) != claim.row_fingerprint:
        row.last_error = _STATE_DRIFT_ERROR
        row.updated_at = now
        db.commit()
        return "pending"
    if delivered:
        row.status = OUTBOX_STATUS_SENT
        row.sent_at = now
        row.last_error = None
        outcome = "sent"
    else:
        row.status = OUTBOX_STATUS_PENDING
        row.last_error = error_code or _NO_ACK_ERROR
        outcome = "pending"
    row.updated_at = now
    db.commit()
    return outcome


def drain(
    db: Session,
    *,
    batch_size: int,
    build_episode: Callable[[GraphEpisodeOutbox], Episode | None],
) -> dict:
    if not graph_client.is_configured():
        return {"status": "unconfigured", "scanned": 0, "sent": 0, "failed": 0}

    excluded_ids: set[int] = set()
    scanned = sent = failed = still_pending = deferred = role_deferred = 0
    for _ in range(max(0, int(batch_size))):
        result = _claim_next(
            db,
            excluded_ids=excluded_ids,
            now=datetime.now(timezone.utc),
            build_episode=build_episode,
        )
        if result is None:
            break
        excluded_ids.add(int(result.row_id))
        if result.outcome == "cooldown":
            deferred += 1
            continue
        scanned += 1
        if result.outcome == "role_deferred":
            deferred += 1
            role_deferred += 1
            continue
        if result.outcome == "failed":
            failed += 1
            continue

        claim = result.claim
        if claim is None:
            raise RuntimeError("graph delivery claim missing provider snapshot")
        if db.in_transaction():
            raise RuntimeError("graph dispatch started in a database transaction")
        try:
            count = episode_module.dispatch(
                [claim.episode],
                db=db,
                bill_organization_id=claim.organization_id,
                bill_role_id=claim.role_id,
                bill_candidate_id=claim.candidate_id,
                bill_trace_id=(
                    f"graph-outbox:{claim.row_id}:{claim.dedup_key}"
                ),
                require_hard_admission=True,
                require_role_admission=True,
                raise_on_error=True,
            )
            delivered = int(count or 0) > 0
            error_code = None if delivered else _NO_ACK_ERROR
        except Exception as exc:
            logger.exception(
                "graph episode dispatch failed row_id=%s error_type=%s",
                claim.row_id,
                type(exc).__name__,
            )
            delivered = False
            error_code = _DISPATCH_ERROR
        outcome = _finalize(
            db,
            claim=claim,
            delivered=delivered,
            error_code=error_code,
            now=datetime.now(timezone.utc),
        )
        if outcome == "sent":
            sent += 1
        else:
            still_pending += 1

    if failed:
        logger.warning(
            "graph_episode_outbox drain: scanned=%d sent=%d failed=%d pending=%d",
            scanned,
            sent,
            failed,
            still_pending,
        )
    return {
        "status": "ok",
        "scanned": scanned,
        "sent": sent,
        "failed": failed,
        "pending": still_pending,
        "deferred": deferred,
        "role_deferred": role_deferred,
    }


__all__ = ["DeliveryClaim", "drain"]
