"""Bounded, deterministic reads for the agent-decision HTTP surface."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, desc, literal, select, union_all
from sqlalchemy.orm import Query, Session

from ...models.agent_decision import AgentDecision
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role


DecisionRow = tuple[AgentDecision, Candidate | None, Role | None]


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
    """Load independently bounded pending/processing lanes in one statement.

    Each lane keeps its own ``limit`` and index-friendly ordering. Wrapping the
    limited lanes before ``UNION ALL`` keeps the SQL valid on SQLite as well as
    PostgreSQL. Most importantly, both lanes share one READ COMMITTED statement
    snapshot, so a concurrent pending-to-processing transition cannot appear in
    both halves of the response.
    """

    pending_lane = _limited_lane(
        query, status="pending", lane_order=0, limit=limit
    )
    processing_lane = _limited_lane(
        query, status="processing", lane_order=1, limit=limit
    )
    limited_queue = union_all(
        select(pending_lane), select(processing_lane)
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


__all__ = ["DecisionRow", "load_agent_decision_rows"]
