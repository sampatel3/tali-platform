"""Short claim phase for provider-gated decision execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .decision_provider_authority import (
    decision_operation_action,
    lock_decision_provider_authority,
    matching_receipt_disposition,
    stable_json_fingerprint,
    validate_decision_provider_preflight,
)
from .decision_provider_call import (
    DecisionProviderPlan,
    resolve_decision_provider_authority,
)
from .decision_provider_operation import (
    DecisionProviderReceiptConflict,
    DecisionProviderSnapshot,
    begin_decision_provider_receipt,
    decision_identity_fingerprint,
    fail_decision_provider_receipt,
    operation_id_for,
)
from .workable_actions_service import WorkableWritebackError


@dataclass(frozen=True)
class DecisionProviderClaim:
    snapshot: DecisionProviderSnapshot
    operation_id: str
    disposition: str
    provider_plan: DecisionProviderPlan | None
    receipt: dict[str, Any]
    expected_role_family: dict[str, Any] | None


def _claim_from_match(
    match: tuple[object, str, dict[str, Any]],
    *,
    expected_role_family: dict[str, Any] | None,
) -> DecisionProviderClaim:
    snapshot, disposition, receipt = match
    assert isinstance(snapshot, DecisionProviderSnapshot)
    return DecisionProviderClaim(
        snapshot=snapshot,
        operation_id=str(receipt["operation_id"]),
        disposition=disposition,
        provider_plan=None,
        receipt=receipt,
        expected_role_family=expected_role_family,
    )


def claim_decision_provider_operation(
    db: Session,
    *,
    organization_id: int,
    decision_id: int,
    disposition: str,
    override_action: str | None,
    note: str | None,
    target_stage: str | None,
    expected_decision_type: str | None,
    expected_role_family: dict[str, Any] | None,
    actor_type: str,
    actor_id: int | None,
    job_run_id: int | None,
) -> DecisionProviderClaim:
    """Persist exact provider authority, then commit and release all locks."""

    locked = lock_decision_provider_authority(
        db,
        organization_id=organization_id,
        decision_id=decision_id,
        expected_decision_type=expected_decision_type,
        expected_role_family=expected_role_family,
        disposition=disposition,
        override_action=override_action,
    )
    action = decision_operation_action(
        disposition=disposition,
        decision_type=str(locked.decision.decision_type),
        override_action=override_action,
    )
    if action is None:
        raise ValueError("decision is not provider-gated")
    match = matching_receipt_disposition(
        app=locked.app,
        decision=locked.decision,
        disposition=disposition,
        action=action,
        override_action=override_action,
        target_stage=target_stage,
    )
    if match is not None:
        claim = _claim_from_match(
            match, expected_role_family=expected_role_family
        )
        try:
            require_no_other_unresolved_provider_operation(
                locked.app,
                receipt_key="decision_provider_operation",
                operation_id=claim.operation_id,
            )
        except UnresolvedProviderOperation as exc:
            db.rollback()
            raise WorkableWritebackError(
                action=action,
                code="ats_operation_conflict",
                message=str(exc),
                retriable=False,
            ) from None
        db.commit()
        return claim

    validate_decision_provider_preflight(
        db,
        authority=locked,
        disposition=disposition,
        override_action=override_action,
    )
    provider = resolve_decision_provider_authority(
        db,
        app=locked.app,
        candidate=locked.candidate,
        organization=locked.organization,
        owner_role=locked.owner_role,
        operation_action=action,
        target_stage=target_stage,
        reason=note,
    )
    evaluation = locked.evaluation
    snapshot = DecisionProviderSnapshot(
        organization_id=int(locked.organization.id),
        application_id=int(locked.app.id),
        expected_application_version=int(locked.app.version or 1),
        expected_application_outcome=str(
            locked.app.application_outcome or "open"
        ).lower(),
        expected_pipeline_stage=str(locked.app.pipeline_stage or "applied").lower(),
        expected_workable_disqualified=bool(locked.app.workable_disqualified),
        candidate_id=int(locked.candidate.id),
        candidate_provider_id=provider.candidate_provider_id,
        decision_id=int(locked.decision.id),
        expected_decision_status=str(locked.decision.status),
        expected_decision_type=str(locked.decision.decision_type),
        decision_identity_fingerprint=decision_identity_fingerprint(locked.decision),
        disposition=disposition,
        operation_action=action,
        override_action=str(override_action or "") or None,
        acting_role_id=int(locked.acting_role.id),
        expected_acting_role_version=int(locked.acting_role.version or 1),
        owner_role_id=int(locked.owner_role.id),
        expected_owner_role_version=int(locked.owner_role.version or 1),
        role_family_fingerprint=stable_json_fingerprint(locked.family_payload),
        workspace_control_version=locked.workspace_version,
        provider=provider.provider,
        provider_target_id=provider.provider_target_id,
        provider_remote_stage=provider.provider_remote_stage,
        target_stage=str(target_stage or "").strip() or None,
        provider_connection_key=provider.provider_connection_key,
        owner_external_job_id=provider.owner_external_job_id,
        related_evaluation_id=int(evaluation.id) if evaluation is not None else None,
        related_evaluation_status=(
            str(evaluation.status or "") if evaluation is not None else None
        ),
        related_pipeline_stage=(
            str(evaluation.pipeline_stage or "") if evaluation is not None else None
        ),
        related_spec_fingerprint=(
            str(evaluation.spec_fingerprint or "") if evaluation is not None else None
        ),
        related_source_application_id=(
            int(evaluation.source_application_id) if evaluation is not None else None
        ),
    )
    operation_id = operation_id_for(snapshot)
    try:
        require_no_other_unresolved_provider_operation(
            locked.app,
            receipt_key="decision_provider_operation",
            operation_id=operation_id,
        )
    except UnresolvedProviderOperation as exc:
        db.rollback()
        raise WorkableWritebackError(
            action=action,
            code="ats_operation_conflict",
            message=str(exc),
            retriable=False,
        ) from None
    if provider.provider == "local":
        return DecisionProviderClaim(
            snapshot=snapshot,
            operation_id=operation_id,
            disposition="local_only",
            provider_plan=None,
            receipt={"local_only_reason": provider.local_only_reason},
            expected_role_family=expected_role_family,
        )
    try:
        receipt, receipt_disposition = begin_decision_provider_receipt(
            locked.app,
            snapshot=snapshot,
            operation_id=operation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=note,
            job_run_id=job_run_id,
        )
    except DecisionProviderReceiptConflict as exc:
        db.rollback()
        raise WorkableWritebackError(
            action=action,
            code="decision_provider_conflict",
            message=str(exc),
            retriable=False,
        ) from None
    if provider.failure is not None and receipt_disposition == "call_provider":
        fail_decision_provider_receipt(
            locked.app,
            operation_id=operation_id,
            code=provider.failure[0],
            message=provider.failure[1],
            provider_called=False,
            retryable=False,
            expected_snapshot_fingerprint=snapshot.fingerprint(),
        )
        db.commit()
        raise WorkableWritebackError(
            action=action,
            code=provider.failure[0],
            message=provider.failure[1],
            retriable=False,
        )
    db.commit()
    return DecisionProviderClaim(
        snapshot=snapshot,
        operation_id=operation_id,
        disposition=receipt_disposition,
        provider_plan=(
            provider.plan if receipt_disposition == "call_provider" else None
        ),
        receipt=receipt,
        expected_role_family=expected_role_family,
    )


__all__ = ["DecisionProviderClaim", "claim_decision_provider_operation"]
