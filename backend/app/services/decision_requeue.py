"""Small shared transition for returning processing decisions to HITL."""

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision


def requeue_processing_decision(
    db: Session, decision_id: int, organization_id: int, *, note: str
) -> None:
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == int(organization_id),
        )
        .first()
    )
    if decision is None or decision.status != "processing":
        return
    decision.status = "pending"
    decision.resolution_note = (note or "")[:500] or None
    db.commit()


__all__ = ["requeue_processing_decision"]
