"""Recruiter endpoints for evidence-backed ATS receipt reconciliation."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from ...services.ats_reconciliation_service import (
    ReceiptIdentity,
    check_ats_reconciliation,
    resolve_ats_reconciliation,
)
from ...services.ats_stage_move_reconciliation import (
    StageReceiptIdentity,
    check_stage_move_reconciliation,
    resolve_stage_move_reconciliation,
)
from ...services.decision_provider_reconciliation import (
    DecisionReceiptIdentity,
    check_decision_provider_reconciliation,
    resolve_decision_provider_reconciliation,
)

router = APIRouter(tags=["ATS reconciliation"])


class AtsReceiptIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_key: str = Field(min_length=1, max_length=100)
    operation_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=32)
    provider_target_id: str = Field(min_length=1, max_length=200)
    acting_role_id: int | None = Field(default=None, gt=0)

    def identity(self) -> ReceiptIdentity:
        return ReceiptIdentity(
            receipt_key=self.receipt_key.strip(),
            operation_id=self.operation_id.strip(),
            provider=self.provider.strip().lower(),
            provider_target_id=self.provider_target_id.strip(),
        )


class AtsReconciliationResolutionRequest(AtsReceiptIdentityRequest):
    observation_id: str = Field(min_length=1, max_length=100)
    disposition: str = Field(min_length=1, max_length=64)


@router.post("/applications/{application_id}/ats-reconciliation/check")
def check_application_ats_reconciliation(
    application_id: int,
    data: AtsReceiptIdentityRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if data.receipt_key.strip() == "decision_provider_operation":
        return check_decision_provider_reconciliation(
            db,
            application_id=application_id,
            identity=DecisionReceiptIdentity(
                operation_id=data.operation_id.strip(),
                provider=data.provider.strip().lower(),
                provider_target_id=data.provider_target_id.strip(),
            ),
            current_user=current_user,
            acting_role_id=data.acting_role_id,
        )
    if data.receipt_key.strip() == "stage_move_operation":
        return check_stage_move_reconciliation(
            db,
            application_id=application_id,
            identity=StageReceiptIdentity(
                operation_id=data.operation_id.strip(),
                provider=data.provider.strip().lower(),
                provider_target_id=data.provider_target_id.strip(),
            ),
            current_user=current_user,
            acting_role_id=data.acting_role_id,
        )
    return check_ats_reconciliation(
        db,
        application_id=application_id,
        identity=data.identity(),
        current_user=current_user,
        acting_role_id=data.acting_role_id,
    )


@router.post("/applications/{application_id}/ats-reconciliation/resolve")
def resolve_application_ats_reconciliation(
    application_id: int,
    data: AtsReconciliationResolutionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if data.receipt_key.strip() == "decision_provider_operation":
        return resolve_decision_provider_reconciliation(
            db,
            application_id=application_id,
            identity=DecisionReceiptIdentity(
                operation_id=data.operation_id.strip(),
                provider=data.provider.strip().lower(),
                provider_target_id=data.provider_target_id.strip(),
            ),
            observation_id=data.observation_id.strip(),
            disposition=data.disposition.strip(),
            current_user=current_user,
            acting_role_id=data.acting_role_id,
        )
    if data.receipt_key.strip() == "stage_move_operation":
        return resolve_stage_move_reconciliation(
            db,
            application_id=application_id,
            identity=StageReceiptIdentity(
                operation_id=data.operation_id.strip(),
                provider=data.provider.strip().lower(),
                provider_target_id=data.provider_target_id.strip(),
            ),
            observation_id=data.observation_id.strip(),
            disposition=data.disposition.strip(),
            current_user=current_user,
            acting_role_id=data.acting_role_id,
        )
    return resolve_ats_reconciliation(
        db,
        application_id=application_id,
        identity=data.identity(),
        observation_id=data.observation_id.strip(),
        disposition=data.disposition.strip(),
        current_user=current_user,
        acting_role_id=data.acting_role_id,
    )


__all__ = ["router"]
