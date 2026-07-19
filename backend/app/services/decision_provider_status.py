"""Read-only status helpers for decision provider receipts."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication


def decision_provider_receipt_for_id(
    db: Session, *, decision_id: int, organization_id: int
) -> dict | None:
    row = (
        db.query(CandidateApplication.integration_sync_state)
        .join(
            AgentDecision,
            AgentDecision.application_id == CandidateApplication.id,
        )
        .filter(
            AgentDecision.id == int(decision_id),
            AgentDecision.organization_id == int(organization_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    state = row.integration_sync_state if row is not None else None
    receipt = state.get("decision_provider_operation") if isinstance(state, dict) else None
    return dict(receipt) if isinstance(receipt, dict) else None


def decision_provider_needs_reconciliation(
    db: Session, *, decision_id: int, organization_id: int
) -> bool:
    receipt = decision_provider_receipt_for_id(
        db, decision_id=decision_id, organization_id=organization_id
    )
    return bool(
        receipt
        and int(receipt.get("decision_id") or 0) == int(decision_id)
        and (
            str(receipt.get("status") or "") == "manual_reconciliation_required"
            or receipt.get("manual_reconciliation_required") is True
            or receipt.get("provider_outcome_uncertain") is True
        )
    )


def decision_provider_confirmed_note_replay(
    db: Session, *, decision_id: int, organization_id: int
) -> bool:
    receipt = decision_provider_receipt_for_id(
        db, decision_id=decision_id, organization_id=organization_id
    )
    post = receipt.get("post_operation") if isinstance(receipt, dict) else None
    return bool(
        receipt
        and int(receipt.get("decision_id") or 0) == int(decision_id)
        and str(receipt.get("status") or "") == "confirmed"
        and isinstance(post, dict)
        and str(post.get("status") or "") != "queued"
    )


__all__ = [
    "decision_provider_confirmed_note_replay",
    "decision_provider_needs_reconciliation",
    "decision_provider_receipt_for_id",
]
