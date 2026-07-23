"""Role-scoped pending-decision projection for shared application rosters."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication


def pending_decision_map(
    db: Session,
    application_ids: list[int],
    *,
    role_id: int,
    statuses: tuple[str, ...] = ("pending",),
) -> dict[int, dict]:
    """Return each candidate's latest visible decision keyed to the shown app.

    Physical application id is not decision identity. The role filter prevents
    owner and sibling pages from rendering one another's recommendations, while
    candidate identity carries a card across its source/transport projection.
    """

    if not application_ids:
        return {}
    application_by_candidate = {
        int(candidate_id): int(application_id)
        for application_id, candidate_id in db.query(
            CandidateApplication.id,
            CandidateApplication.candidate_id,
        )
        .filter(CandidateApplication.id.in_(application_ids))
        .all()
    }
    if not application_by_candidate:
        return {}
    now = datetime.now(timezone.utc)
    row_num = (
        func.row_number()
        .over(
            partition_by=AgentDecision.candidate_id,
            order_by=(AgentDecision.created_at.desc(), AgentDecision.id.desc()),
        )
        .label("rn")
    )
    ranked = (
        db.query(
            AgentDecision.candidate_id.label("candidate_id"),
            AgentDecision.id.label("id"),
            AgentDecision.decision_type.label("decision_type"),
            AgentDecision.recommendation.label("recommendation"),
            AgentDecision.status.label("status"),
            row_num,
        )
        .filter(
            AgentDecision.role_id == int(role_id),
            AgentDecision.candidate_id.in_(application_by_candidate),
            AgentDecision.status.in_(statuses),
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .subquery()
    )
    rows = (
        db.query(
            ranked.c.candidate_id,
            ranked.c.id,
            ranked.c.decision_type,
            ranked.c.recommendation,
            ranked.c.status,
        )
        .filter(ranked.c.rn == 1)
        .all()
    )
    return {
        application_by_candidate[int(candidate_id)]: {
            "id": int(decision_id),
            "decision_type": decision_type,
            "recommendation": recommendation,
            "status": status,
        }
        for candidate_id, decision_id, decision_type, recommendation, status in rows
    }


__all__ = ["pending_decision_map"]
