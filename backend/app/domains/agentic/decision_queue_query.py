"""Bounded, deterministic reads for the agent-decision HTTP surface."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, desc, literal, or_, select, union_all
from sqlalchemy.orm import Query, Session

from ...models.agent_decision import AgentDecision
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role


DecisionRow = tuple[AgentDecision, Candidate | None, Role | None]
LIVE_QUEUE_STATUSES = ("pending", "reverted_for_feedback", "processing")


def _limited_lane(
    query: Query[Any], *, status: str, lane_order: int, limit: int
):
    """Return one filtered queue lane as an ordered, limited subquery."""

    return (
        query.with_entities(
            AgentDecision.id.label("decision_id"),
            AgentDecision.created_at.label("decision_created_at"),
            literal(lane_order).label("lane_order"),
        )
        .filter(AgentDecision.status == status)
        .order_by(desc(AgentDecision.created_at), desc(AgentDecision.id))
        .limit(limit)
        .subquery(f"{status}_decision_lane")
    )


def _load_pending_queue_rows(
    db: Session, query: Query[Any], *, limit: int
) -> list[DecisionRow]:
    """Load independently bounded live lanes in one database statement.

    Each lane keeps its own ``limit`` and index-friendly ordering. Wrapping the
    limited lanes before ``UNION ALL`` keeps the SQL valid on SQLite as well as
    PostgreSQL. Most importantly, all lanes share one READ COMMITTED statement
    snapshot, so a concurrent status transition cannot appear in two parts of
    the response.
    """

    pending_lane = _limited_lane(
        query, status="pending", lane_order=0, limit=limit
    )
    reverted_lane = _limited_lane(
        query, status="reverted_for_feedback", lane_order=1, limit=limit
    )
    processing_lane = _limited_lane(
        query, status="processing", lane_order=2, limit=limit
    )
    limited_queue = union_all(
        select(pending_lane), select(reverted_lane), select(processing_lane)
    ).subquery("limited_decision_queue")

    return (
        db.query(AgentDecision, Candidate, Role)
        .join(
            limited_queue,
            limited_queue.c.decision_id == AgentDecision.id,
        )
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .outerjoin(Role, Role.id == AgentDecision.role_id)
        .order_by(
            limited_queue.c.lane_order,
            desc(limited_queue.c.decision_created_at),
            desc(limited_queue.c.decision_id),
        )
        .all()
    )


def apply_agent_decision_status_filter(
    query: Query[Any],
    *,
    requested_status: str,
    now: datetime | None = None,
) -> Query[Any]:
    """Apply the public queue/history status contract in one place.

    ``pending`` is the Home feed, not a literal database status: ordinary
    pending and taught/reverted cards remain actionable, while processing rows
    are visible read-only receipts. Snooze hides both actionable states but
    never hides an accepted processing receipt.
    """

    if requested_status == "all":
        return query
    if requested_status in ("pending", "reverted_for_feedback"):
        current_time = now or datetime.now(timezone.utc)
        statuses = (
            LIVE_QUEUE_STATUSES
            if requested_status == "pending"
            else ("reverted_for_feedback",)
        )
        return query.filter(AgentDecision.status.in_(statuses)).filter(
            or_(
                AgentDecision.status == "processing",
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= current_time,
            )
        )
    if requested_status == "resolved":
        return query.filter(AgentDecision.status.notin_(LIVE_QUEUE_STATUSES))
    if requested_status == "decided":
        return query.filter(
            AgentDecision.status.in_(("approved", "overridden"))
        )
    if requested_status == "current":
        return query.filter(
            AgentDecision.status.in_(
                LIVE_QUEUE_STATUSES + ("approved", "overridden")
            )
        )
    return query.filter(AgentDecision.status == requested_status)


def load_agent_decision_rows(
    db: Session,
    query: Query[Any],
    *,
    requested_status: str,
    limit: int,
) -> list[DecisionRow]:
    """Execute a filtered decision query with its status-specific ordering."""

    if requested_status == "pending":
        return _load_pending_queue_rows(db, query, limit=limit)
    if requested_status == "current":
        live_first = case(
            (
                AgentDecision.status.in_(
                    ("pending", "processing", "reverted_for_feedback")
                ),
                0,
            ),
            else_=1,
        )
        query = query.order_by(
            live_first, desc(AgentDecision.created_at), desc(AgentDecision.id)
        )
    else:
        query = query.order_by(
            desc(AgentDecision.created_at), desc(AgentDecision.id)
        )
    return query.limit(limit).all()


__all__ = [
    "DecisionRow",
    "LIVE_QUEUE_STATUSES",
    "apply_agent_decision_status_filter",
    "load_agent_decision_rows",
]
