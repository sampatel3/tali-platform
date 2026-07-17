"""Owner-only recovery for unresolved Workable assessment-result delivery."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...deps import require_org_owner
from ...models.user import User
from ...platform.database import get_db
from ...services.assessment_result_delivery_reconciliation import (
    reconcile_assessment_result_delivery,
)

router = APIRouter(tags=["Assessment result delivery"])


class AssessmentResultDeliveryReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm_delivered", "retry_after_provider_absence"]
    expected_operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    provider_result_present_attested: bool = False
    provider_result_absent_attested: bool = False


@router.post("/{assessment_id}/workable-result-delivery/reconcile")
def reconcile_workable_result_delivery(
    assessment_id: int,
    data: AssessmentResultDeliveryReconciliationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
) -> dict:
    return reconcile_assessment_result_delivery(
        db,
        assessment_id=int(assessment_id),
        action=data.action,
        expected_operation_id=data.expected_operation_id,
        provider_result_present_attested=data.provider_result_present_attested,
        provider_result_absent_attested=data.provider_result_absent_attested,
        current_user=current_user,
    )


__all__ = ["router"]
