"""Role-scoped pending-decision projection for shared application rosters."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision


def pending_decision_map(
    db: Session,
    application_ids: list[int],
    *,
    role_id: int,
    statuses: tuple[str, ...] = ("pending",),
) -> dict[int, dict]:
    """Return each application's latest visible decision for exactly one role.

    Related roles reuse the owner's application ids, so application id alone
    is not a decision identity. The role filter prevents owner and sibling
    pages from rendering one another's recommendations.
    """

    if not application_ids:
        return {}
    now = datetime.now(timezone.utc)
    row_num = (
        func.row_number()
        .over(
            partition_by=AgentDecision.application_id,
            order_by=(AgentDecision.created_at.desc(), AgentDecision.id.desc()),
        )
        .label("rn")
    )
    ranked = (
        db.query(
            AgentDecision.application_id.label("application_id"),
            AgentDecision.id.label("id"),
            AgentDecision.decision_type.label("decision_type"),
            AgentDecision.recommendation.label("recommendation"),
            AgentDecision.status.label("status"),
            row_num,
        )
        .filter(
            AgentDecision.role_id == int(role_id),
            AgentDecision.application_id.in_(application_ids),
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
            ranked.c.application_id,
            ranked.c.id,
            ranked.c.decision_type,
            ranked.c.recommendation,
            ranked.c.status,
        )
        .filter(ranked.c.rn == 1)
        .all()
    )
    return {
        int(app_id): {
            "id": int(decision_id),
            "decision_type": decision_type,
            "recommendation": recommendation,
            "status": status,
        }
        for app_id, decision_id, decision_type, recommendation, status in rows
    }


__all__ = ["pending_decision_map"]
