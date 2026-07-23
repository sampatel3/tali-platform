"""Canonical admission boundary for recruiter-facing agent decisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Query, Session

from ..models.agent_decision import (
    AGENT_DECISION_ACTIVE_STATUSES,
    AgentDecision,
)
from ..models.candidate_application import CandidateApplication


def lock_decision_application(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
) -> CandidateApplication | None:
    """Serialize every producer on the durable application subject.

    The logical role remains part of the dedupe query, so related roles that
    share one candidate retain independent queue slots. Locking the physical
    row serializes same-application writers; the candidate-keyed partial unique
    index remains the final invariant for owner/direct application races and
    legacy writers that do not pass through this service.
    """

    query = db.query(CandidateApplication).filter(
        CandidateApplication.id == int(application_id),
        CandidateApplication.organization_id == int(organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(of=CandidateApplication)
    return query.populate_existing().one_or_none()


def active_decision_query(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
) -> Query:
    """Return the canonical role/candidate query used by every producer."""

    candidate_id = (
        select(CandidateApplication.candidate_id)
        .where(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .scalar_subquery()
    )

    return db.query(AgentDecision).filter(
        AgentDecision.organization_id == int(organization_id),
        AgentDecision.role_id == int(role_id),
        AgentDecision.candidate_id == candidate_id,
        AgentDecision.status.in_(AGENT_DECISION_ACTIVE_STATUSES),
    )


def latest_active_decision(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
) -> AgentDecision | None:
    """Load the surviving active decision for an exact logical subject."""

    return (
        active_decision_query(
            db,
            organization_id=organization_id,
            role_id=role_id,
            application_id=application_id,
        )
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .first()
    )


__all__ = [
    "active_decision_query",
    "latest_active_decision",
    "lock_decision_application",
]
