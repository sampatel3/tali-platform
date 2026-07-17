"""Exact locked authority for Decision Hub ATS reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .decision_provider_authority import lock_decision_provider_authority
from .decision_provider_call import resolve_decision_provider_authority
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_drift import decision_provider_drift_reason
from .decision_provider_observation import (
    DecisionProviderObservationFailure,
    DecisionProviderObservationPlan,
    decision_provider_observation_plan,
)
from .decision_provider_operation import (
    DECISION_PROVIDER_HISTORY_KEY,
    DECISION_PROVIDER_OPERATION_KEY,
    DecisionProviderSnapshot,
    snapshot_from_receipt,
)


RECONCILABLE_DECISION_STATUSES = frozenset(
    {
        "provider_call_started",
        "provider_succeeded",
        "manual_reconciliation_required",
    }
)


@dataclass(frozen=True)
class DecisionReceiptIdentity:
    operation_id: str
    provider: str
    provider_target_id: str


@dataclass(frozen=True)
class DecisionReconciliationAuthority:
    claim: DecisionProviderClaim
    receipt: dict[str, Any]
    location: str
    observation_plan: DecisionProviderObservationPlan


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _identity(receipt: dict[str, Any]) -> DecisionReceiptIdentity:
    return DecisionReceiptIdentity(
        operation_id=str(receipt.get("operation_id") or "").strip(),
        provider=str(receipt.get("provider") or "").strip().lower(),
        provider_target_id=str(receipt.get("provider_target_id") or "").strip(),
    )


def locate_decision_reconciliation_receipt(
    app: CandidateApplication, identity: DecisionReceiptIdentity
) -> tuple[dict[str, Any], str]:
    state = (
        app.integration_sync_state
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    current = state.get(DECISION_PROVIDER_OPERATION_KEY)
    if isinstance(current, dict) and _identity(current) == identity:
        return dict(current), "current"
    history = state.get(DECISION_PROVIDER_HISTORY_KEY)
    if isinstance(history, list):
        for item in reversed(history):
            if isinstance(item, dict) and _identity(item) == identity:
                return dict(item), "history"
    raise HTTPException(status_code=404, detail="Exact decision ATS receipt not found")


def decision_reconciliation_snapshot(
    receipt: dict[str, Any],
) -> DecisionProviderSnapshot:
    try:
        snapshot = snapshot_from_receipt(receipt)
        fingerprint = snapshot.fingerprint()
    except (TypeError, ValueError):
        raise _conflict(
            "This legacy decision receipt lacks an exact authority snapshot"
        ) from None
    if fingerprint != str(receipt.get("snapshot_fingerprint") or ""):
        raise _conflict("The decision ATS authority snapshot is malformed")
    return snapshot


def _require_unresolved(receipt: dict[str, Any]) -> None:
    status = str(receipt.get("status") or "").strip().lower()
    if status not in RECONCILABLE_DECISION_STATUSES and not (
        receipt.get("provider_outcome_uncertain") is True
        or receipt.get("manual_reconciliation_required") is True
    ):
        raise _conflict("This exact decision ATS write does not require reconciliation")


def lock_decision_reconciliation_authority(
    db: Session,
    *,
    app: CandidateApplication,
    identity: DecisionReceiptIdentity,
    acting_role_id: int | None,
) -> DecisionReconciliationAuthority:
    receipt, location = locate_decision_reconciliation_receipt(app, identity)
    if location != "current":
        raise _conflict(
            "Archived decision evidence can be inspected but cannot overwrite a newer operation"
        )
    _require_unresolved(receipt)
    snapshot = decision_reconciliation_snapshot(receipt)
    if acting_role_id is not None and int(acting_role_id) != int(
        snapshot.acting_role_id or 0
    ):
        raise _conflict(
            "The reconciliation role does not match the exact decision receipt"
        )
    if (
        int(snapshot.application_id) != int(app.id)
        or int(snapshot.organization_id) != int(app.organization_id)
    ):
        raise _conflict("The decision receipt does not own this application")
    current = lock_decision_provider_authority(
        db,
        organization_id=snapshot.organization_id,
        decision_id=snapshot.decision_id,
        expected_decision_type=snapshot.expected_decision_type,
        expected_role_family=None,
        disposition=snapshot.disposition,
        override_action=snapshot.override_action,
    )
    provider = resolve_decision_provider_authority(
        db,
        app=current.app,
        candidate=current.candidate,
        organization=current.organization,
        owner_role=current.owner_role,
        operation_action=snapshot.operation_action,
        target_stage=snapshot.target_stage,
        reason=str(receipt.get("reason") or "") or None,
    )
    claim = DecisionProviderClaim(
        snapshot=snapshot,
        operation_id=identity.operation_id,
        disposition="reconciliation_required",
        provider_plan=None,
        receipt=receipt,
        expected_role_family=None,
    )
    drift = decision_provider_drift_reason(
        claim=claim,
        current=current,
        provider=provider,
        expected_receipt_statuses=RECONCILABLE_DECISION_STATUSES,
    )
    if drift is not None:
        raise _conflict(
            "Local decision authority changed after the ATS write "
            f"({drift}); it cannot be auto-aligned."
        )
    if provider.failure is not None or provider.plan is None:
        raise _conflict("The exact ATS provider authority is no longer available")
    try:
        observation_plan = decision_provider_observation_plan(provider.plan)
    except DecisionProviderObservationFailure as exc:
        raise _conflict(exc.message) from None
    return DecisionReconciliationAuthority(
        claim=claim,
        receipt=receipt,
        location=location,
        observation_plan=observation_plan,
    )


__all__ = [
    "DecisionReceiptIdentity",
    "DecisionReconciliationAuthority",
    "decision_reconciliation_snapshot",
    "locate_decision_reconciliation_receipt",
    "lock_decision_reconciliation_authority",
]
