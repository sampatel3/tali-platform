"""Locked application, receipt, and authority context for stage reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.user import User
from .ats_reconciliation_authority import lock_reconciliation_application
from .ats_stage_move_provider import StageMoveObservationPlan
from .ats_stage_move_receipt import (
    STAGE_MOVE_OPERATION_KEY,
    StageMoveSnapshot,
)
from .ats_stage_move_reconciliation_authority import (
    lock_stage_reconciliation_authority,
)
from .ats_stage_move_reconciliation_history import (
    ArchivedStageMoveHistoryError,
    locate_archived_stage_move_receipt,
)


@dataclass(frozen=True)
class StageReceiptIdentity:
    operation_id: str
    provider: str
    provider_target_id: str


ValidationResult = TypeVar("ValidationResult")


@dataclass(frozen=True)
class LockedStageReconciliation(Generic[ValidationResult]):
    application: CandidateApplication
    receipt: dict[str, Any]
    location: str
    snapshot: StageMoveSnapshot
    observation_plan: StageMoveObservationPlan
    validation: ValidationResult


def _receipt_identity(receipt: dict[str, Any]) -> StageReceiptIdentity:
    return StageReceiptIdentity(
        operation_id=str(receipt.get("operation_id") or "").strip(),
        provider=str(receipt.get("provider") or "").strip().lower(),
        provider_target_id=str(receipt.get("provider_target_id") or "").strip(),
    )


def _locate_receipt(
    application: CandidateApplication,
    identity: StageReceiptIdentity,
) -> tuple[dict[str, Any], str]:
    state = (
        application.integration_sync_state
        if isinstance(application.integration_sync_state, dict)
        else {}
    )
    current = state.get(STAGE_MOVE_OPERATION_KEY)
    if isinstance(current, dict) and _receipt_identity(current) == identity:
        return dict(current), "current"
    try:
        archived = locate_archived_stage_move_receipt(
            state,
            operation_id=identity.operation_id,
            provider=identity.provider,
            provider_target_id=identity.provider_target_id,
        )
    except ArchivedStageMoveHistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if archived is not None:
        return archived, "history"
    raise HTTPException(status_code=404, detail="Exact ATS stage-move receipt not found")


def lock_stage_reconciliation_context(
    db: Session,
    *,
    application_id: int,
    identity: StageReceiptIdentity,
    current_user: User,
    acting_role_id: int | None,
    validate_receipt: Callable[[dict[str, Any], str], ValidationResult],
) -> LockedStageReconciliation[ValidationResult]:
    """Preauthorize without role locks, validate, then lock and reauthorize."""

    application = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
        lock_role_for_update=False,
        allow_globally_advanced=True,
    )
    receipt, location = _locate_receipt(application, identity)
    validation = validate_receipt(receipt, location)
    snapshot, observation_plan = lock_stage_reconciliation_authority(
        db,
        app=application,
        receipt=receipt,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    return LockedStageReconciliation(
        application=application,
        receipt=receipt,
        location=location,
        snapshot=snapshot,
        observation_plan=observation_plan,
        validation=validation,
    )


__all__ = [
    "LockedStageReconciliation",
    "StageReceiptIdentity",
    "lock_stage_reconciliation_context",
]
