"""Bounded, fair candidate selection for the graph episode outbox."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Text, and_, case, cast, func, or_
from sqlalchemy.orm import Query, Session, defer
from sqlalchemy.orm.attributes import set_committed_value

from ..models.graph_episode_outbox import (
    GRAPH_EPISODE_KINDS,
    OUTBOX_STATUS_PENDING,
    GraphEpisodeOutbox,
)
from ..models.organization import Organization
from ..models.role import Role


RETRY_DELAYS_SECONDS = (300, 600, 1_200, 2_400, 3_600)


def retry_delay_seconds(attempts: int) -> int:
    if int(attempts) <= 0:
        return 0
    index = min(int(attempts), len(RETRY_DELAYS_SECONDS)) - 1
    return RETRY_DELAYS_SECONDS[index]


def _retry_due_expression(*, now: datetime):
    attempts = func.coalesce(GraphEpisodeOutbox.attempts, 0)
    cutoff = case(
        *(
            (
                attempts == attempt,
                now - timedelta(seconds=delay_seconds),
            )
            for attempt, delay_seconds in enumerate(
                RETRY_DELAYS_SECONDS[:-1],
                start=1,
            )
        ),
        else_=now - timedelta(seconds=RETRY_DELAYS_SECONDS[-1]),
    )
    return or_(
        attempts <= 0,
        GraphEpisodeOutbox.updated_at.is_(None),
        GraphEpisodeOutbox.updated_at <= cutoff,
    )


def pending_outbox_query(
    db: Session,
    *,
    now: datetime,
    batch_size: int,
) -> Query:
    """Build one bounded query whose pre-limit order prevents starvation."""
    bounded_batch_size = max(int(batch_size), 0)
    retry_due = _retry_due_expression(now=now)
    active_role = and_(
        Role.id.isnot(None),
        Role.agentic_mode_enabled.is_(True),
        Role.agent_paused_at.is_(None),
        Organization.id.isnot(None),
        Organization.agent_workspace_paused_at.is_(None),
    )
    return (
        db.query(
            GraphEpisodeOutbox,
            cast(GraphEpisodeOutbox.payload, Text).label("payload_text"),
        )
        .options(defer(GraphEpisodeOutbox.payload))
        .outerjoin(
            Role,
            and_(
                Role.id == GraphEpisodeOutbox.role_id,
                Role.organization_id == GraphEpisodeOutbox.organization_id,
                Role.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            Organization,
            Organization.id == GraphEpisodeOutbox.organization_id,
        )
        .filter(
            GraphEpisodeOutbox.status == OUTBOX_STATUS_PENDING,
            GraphEpisodeOutbox.episode_kind.in_(GRAPH_EPISODE_KINDS),
            retry_due,
            # A missing joined role represents either a rolling-deploy legacy
            # NULL (repair once) or invalid ownership (fail once). Normalized
            # held rows stay durable without being repeatedly locked/decoded.
            or_(Role.id.is_(None), active_role),
        )
        .order_by(
            GraphEpisodeOutbox.updated_at.asc(),
            GraphEpisodeOutbox.id.asc(),
        )
        .limit(bounded_batch_size)
        .with_for_update(of=GraphEpisodeOutbox, skip_locked=True)
    )


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _decode_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        payload = json.loads(
            value,
            parse_float=_finite_json_float,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def lock_pending_outbox_rows(
    db: Session,
    *,
    now: datetime,
    batch_size: int,
) -> list[GraphEpisodeOutbox]:
    locked = pending_outbox_query(db, now=now, batch_size=batch_size).all()
    rows: list[GraphEpisodeOutbox] = []
    for row, payload_text in locked:
        # Loading JSON as text keeps pathological legacy values behind the
        # guarded decoder. set_committed_value avoids rewriting the payload.
        set_committed_value(row, "payload", _decode_payload(payload_text))
        rows.append(row)
    return rows


__all__ = [
    "lock_pending_outbox_rows",
    "pending_outbox_query",
    "retry_delay_seconds",
]
