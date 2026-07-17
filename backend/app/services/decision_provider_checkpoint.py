"""Post-provider checkpoints and reconciliation evidence for decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .decision_provider_call import DecisionProviderFailure
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_operation import (
    checkpoint_decision_provider_success,
    fail_decision_provider_receipt,
    mark_decision_provider_reconciliation,
)


def lock_claim_application(
    db: Session, claim: DecisionProviderClaim
) -> CandidateApplication | None:
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == claim.snapshot.application_id,
            CandidateApplication.organization_id == claim.snapshot.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )


def append_decision_reconciliation_event(
    db: Session,
    *,
    app: CandidateApplication,
    claim: DecisionProviderClaim,
    reason: str,
    provider_succeeded: bool | None,
) -> None:
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_decision_reconciliation_required",
        actor_type="system",
        reason="The ATS decision result needs exact verification",
        metadata={
            "operation_id": claim.operation_id,
            "decision_id": claim.snapshot.decision_id,
            "operation_action": claim.snapshot.operation_action,
            "provider": claim.snapshot.provider,
            "provider_target_id": claim.snapshot.provider_target_id,
            "drift_reason": reason,
            "provider_succeeded": provider_succeeded,
            "local_state_preserved": True,
        },
        idempotency_key=f"{claim.operation_id}:reconciliation"[:200],
    )


def mark_claim_reconciliation(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    app: CandidateApplication,
    reason: str,
    provider_succeeded: bool | None,
) -> dict[str, Any]:
    mark_decision_provider_reconciliation(
        app,
        snapshot=claim.snapshot,
        operation_id=claim.operation_id,
        reason=reason,
        provider_called=(True if provider_succeeded is True else None),
        provider_succeeded=provider_succeeded,
    )
    append_decision_reconciliation_event(
        db,
        app=app,
        claim=claim,
        reason=reason,
        provider_succeeded=provider_succeeded,
    )
    db.commit()
    return {
        "status": "reconciliation_required",
        "decision_id": claim.snapshot.decision_id,
        "application_id": claim.snapshot.application_id,
        "operation_id": claim.operation_id,
        "reconciliation_reason": reason,
        "failed": True,
    }


def record_decision_provider_failure(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    error: DecisionProviderFailure,
) -> None:
    app = lock_claim_application(db, claim)
    if app is None:
        db.rollback()
        return
    updated = fail_decision_provider_receipt(
        app,
        operation_id=claim.operation_id,
        code=error.code,
        message=error.message,
        provider_called=error.provider_called,
        retryable=error.retriable,
        expected_snapshot_fingerprint=claim.snapshot.fingerprint(),
    )
    if updated is None:
        mark_decision_provider_reconciliation(
            app,
            snapshot=claim.snapshot,
            operation_id=claim.operation_id,
            reason=f"provider_failure_receipt_changed:{error.code}",
            provider_called=error.provider_called,
            provider_succeeded=(False if error.provider_called is False else None),
        )
    if error.provider_called is None:
        append_decision_reconciliation_event(
            db,
            app=app,
            claim=claim,
            reason=f"provider_failure:{error.code}",
            provider_succeeded=None,
        )
    db.commit()


def checkpoint_claim_success(
    db: Session,
    *,
    claim: DecisionProviderClaim,
    provider_result: dict[str, Any],
) -> bool:
    app = lock_claim_application(db, claim)
    if app is None:
        db.rollback()
        return False
    checkpoint = checkpoint_decision_provider_success(
        app,
        operation_id=claim.operation_id,
        expected_snapshot_fingerprint=claim.snapshot.fingerprint(),
        provider_result=provider_result,
    )
    if checkpoint is None:
        mark_decision_provider_reconciliation(
            app,
            snapshot=claim.snapshot,
            operation_id=claim.operation_id,
            reason="provider_success_receipt_changed",
            provider_called=True,
            provider_succeeded=True,
        )
        append_decision_reconciliation_event(
            db,
            app=app,
            claim=claim,
            reason="provider_success_receipt_changed",
            provider_succeeded=True,
        )
        db.commit()
        return False
    db.commit()
    return True


__all__ = [
    "append_decision_reconciliation_event",
    "checkpoint_claim_success",
    "lock_claim_application",
    "mark_claim_reconciliation",
    "record_decision_provider_failure",
]
