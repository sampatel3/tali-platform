"""Exact read-and-finalize recovery for ambiguous Decision Hub ATS writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..actions.types import Actor
from ..models.candidate_application import CandidateApplication
from ..models.user import User
from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .ats_reconciliation_authority import lock_reconciliation_application
from .decision_provider_authority import lock_decision_provider_authority
from .decision_provider_call import resolve_decision_provider_authority
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_drift import decision_provider_drift_reason
from .decision_provider_finalize import finalize_decision_provider_success
from .decision_provider_observation import (
    DecisionProviderObservationFailure,
    DecisionProviderObservationPlan,
    decision_provider_observation_plan,
    perform_decision_provider_observation,
)
from .decision_provider_operation import (
    DECISION_PROVIDER_HISTORY_KEY,
    DECISION_PROVIDER_OPERATION_KEY,
    DecisionProviderSnapshot,
    snapshot_from_receipt,
)
from .decision_provider_post_operation import (
    emit_decision_graph_episode,
    queue_decision_post_operation,
)
from .decision_provider_reconciliation_evidence import (
    complete_decision_reconciliation_audit,
)
from .document_service import sanitize_json_for_storage
from .reconciliation_history import (
    append_reconciliation_history_or_conflict,
    require_reconciliation_history_capacity_or_conflict,
)

_OBSERVATION_MAX_AGE = timedelta(minutes=5)
_RECONCILABLE_STATUSES = frozenset(
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
class _Authority:
    claim: DecisionProviderClaim
    receipt: dict[str, Any]
    location: str
    observation_plan: DecisionProviderObservationPlan


DecisionObservation = Callable[
    [DecisionProviderObservationPlan], dict[str, Any]
]


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _identity(receipt: dict[str, Any]) -> DecisionReceiptIdentity:
    return DecisionReceiptIdentity(
        operation_id=str(receipt.get("operation_id") or "").strip(),
        provider=str(receipt.get("provider") or "").strip().lower(),
        provider_target_id=str(receipt.get("provider_target_id") or "").strip(),
    )


def _locate(
    app: CandidateApplication, identity: DecisionReceiptIdentity
) -> tuple[dict[str, Any], str]:
    state = app.integration_sync_state if isinstance(app.integration_sync_state, dict) else {}
    current = state.get(DECISION_PROVIDER_OPERATION_KEY)
    if isinstance(current, dict) and _identity(current) == identity:
        return dict(current), "current"
    history = state.get(DECISION_PROVIDER_HISTORY_KEY)
    if isinstance(history, list):
        for item in reversed(history):
            if isinstance(item, dict) and _identity(item) == identity:
                return dict(item), "history"
    raise HTTPException(status_code=404, detail="Exact decision ATS receipt not found")


def _snapshot(receipt: dict[str, Any]) -> DecisionProviderSnapshot:
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
    if status not in _RECONCILABLE_STATUSES and not (
        receipt.get("provider_outcome_uncertain") is True
        or receipt.get("manual_reconciliation_required") is True
    ):
        raise _conflict("This exact decision ATS write does not require reconciliation")


def _lock_authority(
    db: Session,
    *,
    app: CandidateApplication,
    identity: DecisionReceiptIdentity,
) -> _Authority:
    receipt, location = _locate(app, identity)
    if location != "current":
        raise _conflict(
            "Archived decision evidence can be inspected but cannot overwrite a newer operation"
        )
    _require_unresolved(receipt)
    snapshot = _snapshot(receipt)
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
        expected_receipt_statuses=_RECONCILABLE_STATUSES,
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
    return _Authority(
        claim=claim,
        receipt=receipt,
        location=location,
        observation_plan=observation_plan,
    )


def _store_receipt(
    app: CandidateApplication, receipt: dict[str, Any]
) -> None:
    state = dict(app.integration_sync_state or {})
    state[DECISION_PROVIDER_OPERATION_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)


def check_decision_provider_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: DecisionReceiptIdentity,
    current_user: User,
    acting_role_id: int | None = None,
    observe: DecisionObservation = perform_decision_provider_observation,
) -> dict[str, Any]:
    """Observe the exact ATS target with no transaction or row lock held."""

    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    authority = _lock_authority(db, app=app, identity=identity)
    require_reconciliation_history_capacity_or_conflict(
        authority.receipt, "reconciliation_observation_history"
    )
    require_reconciliation_history_capacity_or_conflict(
        authority.receipt, "reconciliation_resolution_history"
    )
    source_status = str(authority.receipt.get("status") or "")
    source_updated_at = str(authority.receipt.get("updated_at") or "")
    snapshot_fingerprint = authority.claim.snapshot.fingerprint()
    db.commit()
    if db.in_transaction():
        raise RuntimeError("Decision ATS observation cannot run in a DB transaction")
    try:
        remote = observe(authority.observation_plan)
    except DecisionProviderObservationFailure as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    if not isinstance(remote, dict) or not remote.get("success"):
        raise HTTPException(status_code=502, detail="ATS returned an invalid observation")

    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    current = _lock_authority(db, app=app, identity=identity)
    if (
        str(current.receipt.get("status") or "") != source_status
        or str(current.receipt.get("updated_at") or "") != source_updated_at
        or current.claim.snapshot.fingerprint() != snapshot_fingerprint
    ):
        raise _conflict("The decision ATS receipt changed during the provider check")
    checked_at = _now().isoformat()
    observation = sanitize_json_for_storage(
        {
            **remote,
            "observation_id": uuid4().hex,
            "receipt_key": DECISION_PROVIDER_OPERATION_KEY,
            "operation_id": identity.operation_id,
            "provider": identity.provider,
            "provider_target_id": identity.provider_target_id,
            "operation_action": current.claim.snapshot.operation_action,
            "expected_remote_stage": current.claim.snapshot.provider_remote_stage,
            "checked_at": checked_at,
            "checked_by_actor_id": int(current_user.id),
            "observed_receipt_status": source_status,
            "observed_receipt_updated_at": source_updated_at,
            "snapshot_fingerprint": snapshot_fingerprint,
        }
    )
    receipt = dict(current.receipt)
    receipt["reconciliation_observation"] = observation
    receipt["reconciliation_last_checked_at"] = checked_at
    append_reconciliation_history_or_conflict(
        receipt,
        history_key="reconciliation_observation_history",
        entry=observation,
        saturated_at=checked_at,
    )
    _store_receipt(app, receipt)
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_decision_reconciliation_observed",
        actor_type="recruiter",
        actor_id=int(current_user.id),
        reason="Recruiter checked the exact unresolved decision ATS write",
        metadata=observation,
        idempotency_key=f"decision-observation:{observation['observation_id']}"[:200],
    )
    db.commit()
    return observation


def _checked_at(observation: dict[str, Any]) -> datetime:
    try:
        value = datetime.fromisoformat(str(observation.get("checked_at") or ""))
    except ValueError:
        raise _conflict("The decision ATS observation timestamp is invalid") from None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_decision_provider_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: DecisionReceiptIdentity,
    observation_id: str,
    disposition: str,
    current_user: User,
    acting_role_id: int | None = None,
) -> dict[str, Any]:
    """Finish the local decision only after an exact matching ATS observation."""

    if disposition != "confirm_decision_provider_effect":
        raise HTTPException(status_code=422, detail="Unsupported decision resolution")
    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    receipt, location = _locate(app, identity)
    if location != "current":
        raise _conflict("Archived decision evidence cannot alter current local state")
    observation = receipt.get("reconciliation_observation")
    if not isinstance(observation, dict) or str(observation.get("observation_id") or "") != observation_id:
        raise _conflict("That exact decision ATS observation is no longer current")
    if str(receipt.get("status") or "").lower() == "confirmed":
        pending = receipt.get("reconciliation_pending")
        if isinstance(pending, dict) and str(pending.get("observation_id") or "") == observation_id:
            return complete_decision_reconciliation_audit(
                db,
                app=app,
                receipt=receipt,
                observation=observation,
                current_user=current_user,
            )
        evidence = receipt.get("reconciliation_evidence")
        if (
            isinstance(evidence, dict)
            and str(evidence.get("observation_id") or "") == observation_id
        ):
            return evidence
        raise _conflict("This decision ATS operation was already confirmed differently")
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_resolution_history"
    )
    authority = _lock_authority(db, app=app, identity=identity)
    receipt = dict(authority.receipt)
    observation = receipt.get("reconciliation_observation")
    if not isinstance(observation, dict) or any(
        str(observation.get(key) or "") != expected
        for key, expected in (
            ("observation_id", observation_id),
            ("operation_id", identity.operation_id),
            ("provider", identity.provider),
            ("provider_target_id", identity.provider_target_id),
            ("snapshot_fingerprint", authority.claim.snapshot.fingerprint()),
            ("observed_receipt_updated_at", str(receipt.get("updated_at") or "")),
        )
    ):
        raise _conflict("The ATS observation does not match this exact decision receipt")
    if _now() - _checked_at(observation) > _OBSERVATION_MAX_AGE:
        raise _conflict("The decision ATS observation is stale; check it again")
    if observation.get("provider_effect_matches") is not True:
        raise _conflict(
            "The ATS does not show the exact requested effect. The original write remains "
            "ambiguous and is not safe to retry automatically."
        )
    try:
        require_no_other_unresolved_provider_operation(
            app,
            receipt_key=DECISION_PROVIDER_OPERATION_KEY,
            operation_id=identity.operation_id,
        )
    except UnresolvedProviderOperation as exc:
        raise _conflict(str(exc)) from None

    now = _now().isoformat()
    provider_result = {
        "success": True,
        "code": "reconciled_observation",
        "provider": identity.provider,
        "provider_remote_stage": (
            authority.claim.snapshot.provider_remote_stage
            or observation.get("provider_remote_stage")
        ),
    }
    receipt.update(
        status="provider_succeeded",
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        provider_remote_stage=provider_result["provider_remote_stage"],
        provider_result=provider_result,
        reconciliation_pending={
            "observation_id": observation_id,
            "disposition": disposition,
            "resolved_by_actor_id": int(current_user.id),
            "requested_at": now,
        },
        updated_at=now,
    )
    _store_receipt(app, receipt)
    # The finalizer re-queries with ``populate_existing`` to acquire the full
    # authority lock set. Flush this same phase-C receipt transition first so
    # that refresh cannot restore the pre-observation ambiguous status from
    # the database over the in-memory provider-success checkpoint.
    db.flush()
    claim = DecisionProviderClaim(
        snapshot=authority.claim.snapshot,
        operation_id=identity.operation_id,
        disposition="finalize_provider_success",
        provider_plan=None,
        receipt=receipt,
        expected_role_family=None,
    )
    actor = Actor.recruiter(current_user)
    result, post = finalize_decision_provider_success(
        db,
        claim=claim,
        provider_result=provider_result,
        actor=actor,
        note=str(receipt.get("reason") or "") or None,
        target_stage=claim.snapshot.target_stage,
        override_action=claim.snapshot.override_action,
    )
    if result.get("status") != "ok":
        return result
    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    receipt, location = _locate(app, identity)
    if location != "current" or str(receipt.get("status") or "") != "confirmed":
        raise _conflict("The confirmed decision receipt changed before audit completion")
    evidence = complete_decision_reconciliation_audit(
        db,
        app=app,
        receipt=receipt,
        observation=observation,
        current_user=current_user,
    )
    queue_decision_post_operation(db, claim=claim, post=post)
    emit_decision_graph_episode(
        claim=claim,
        actor=actor,
        note=str(receipt.get("reason") or "") or None,
    )
    return {**result, "reconciliation": evidence}


__all__ = [
    "DecisionReceiptIdentity",
    "check_decision_provider_reconciliation",
    "resolve_decision_provider_reconciliation",
]
