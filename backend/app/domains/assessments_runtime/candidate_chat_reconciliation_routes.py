"""Owner-only visibility and no-replay recovery for candidate chat claims."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...deps import require_org_owner
from ...models.user import User
from ...platform.database import get_db
from ...services.candidate_chat_reconciliation import (
    list_candidate_chat_reconciliation_operations,
    reconcile_candidate_chat_operation,
)
from .workspace_serialization import (
    assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)

router = APIRouter(tags=["Candidate chat reconciliation"])


class CandidateChatReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["close_without_replay"]
    expected_request_reference: str = Field(
        min_length=40,
        max_length=40,
        pattern=r"^chatreq_[a-f0-9]{32}$",
    )
    provider_outcome_discarded_attested: bool = False


@router.get("/{assessment_id}/candidate-chat-reconciliations")
def list_candidate_chat_reconciliations(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
) -> dict:
    return list_candidate_chat_reconciliation_operations(
        db,
        assessment_id=int(assessment_id),
        organization_id=int(current_user.organization_id),
    )


@router.post(
    "/{assessment_id}/candidate-chat-reconciliations/{operation_id}/resolve"
)
def resolve_candidate_chat_reconciliation(
    assessment_id: int,
    operation_id: Annotated[
        str,
        Path(
            min_length=72,
            max_length=72,
            pattern=r"^chatrec_[a-f0-9]{64}$",
        ),
    ],
    data: CandidateChatReconciliationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
) -> dict:
    organization_id = int(current_user.organization_id)
    actor_id = int(current_user.id)
    prepare_assessment_workspace_mutex(db)
    with assessment_workspace_mutex(db, assessment_id=int(assessment_id)):
        try:
            return reconcile_candidate_chat_operation(
                db,
                assessment_id=int(assessment_id),
                organization_id=organization_id,
                actor_id=actor_id,
                operation_id=str(operation_id),
                expected_request_reference=data.expected_request_reference,
                action=data.action,
                provider_outcome_discarded_attested=(
                    data.provider_outcome_discarded_attested
                ),
            )
        except Exception:
            db.rollback()
            raise


__all__ = ["router"]
