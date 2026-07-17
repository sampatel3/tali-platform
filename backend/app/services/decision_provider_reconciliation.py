"""Exact read-and-finalize recovery for ambiguous Decision Hub ATS writes."""

from __future__ import annotations

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
from .decision_provider_claim import DecisionProviderClaim
from .decision_provider_finalize import finalize_decision_provider_success
from .decision_provider_observation import (
    DecisionProviderObservationFailure,
    DecisionProviderObservationPlan,
)
from .decision_provider_operation import (
    DECISION_PROVIDER_OPERATION_KEY,
)
from .decision_provider_post_operation import (
    emit_decision_graph_episode,
    queue_decision_post_operation,
)
from .decision_provider_reconciliation_evidence import (
    complete_decision_reconciliation_audit,
)
from .decision_provider_reconciliation_authority import (
    DecisionReceiptIdentity,
    DecisionReconciliationAuthority,
    decision_reconciliation_snapshot as _snapshot,
    locate_decision_reconciliation_receipt as _locate,
    lock_decision_reconciliation_authority as _lock_authority,
)
from .document_service import sanitize_json_for_storage
from .reconciliation_history import (
    append_reconciliation_history_or_conflict,
    require_reconciliation_history_capacity_or_conflict,
)

_OBSERVATION_MAX_AGE = timedelta(minutes=5)


DecisionObservation = Callable[
    [DecisionProviderObservationPlan], dict[str, Any]
]


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reauthorize_locked_application(
    db: Session,
    *,
    app: CandidateApplication,
    current_user: User,
    acting_role_id: int | None,
) -> None:
    from ..domains.assessments_runtime.related_role_actions import (
        authorize_locked_application_edit,
    )

    # The initial application-first precheck deliberately avoids a role lock.
    # The decision authority loader then acquires workspace and family locks in
    # their canonical order. Recheck under those held locks so a concurrent
    # hiring-team revocation cannot cross the gap.
    authorize_locked_application_edit(
        db,
        current_user=current_user,
        acting_role_id=acting_role_id,
        locked_application=app,
        allow_already_rejected=True,
        allow_globally_advanced=True,
    )


def _lock_authorized_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: DecisionReceiptIdentity,
    current_user: User,
    acting_role_id: int | None,
) -> tuple[CandidateApplication, DecisionReconciliationAuthority]:
    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
        lock_role_for_update=False,
        allow_globally_advanced=True,
    )
    authority = _lock_authority(
        db,
        app=app,
        identity=identity,
        acting_role_id=acting_role_id,
    )
    _reauthorize_locked_application(
        db,
        app=app,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    return app, authority


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
    observe: DecisionObservation | None = None,
) -> dict[str, Any]:
    """Observe the exact ATS target with no transaction or row lock held."""

    app, authority = _lock_authorized_reconciliation(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
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
    if observe is None:
        from .decision_provider_observation import (
            perform_decision_provider_observation,
        )

        observe = perform_decision_provider_observation
    try:
        remote = observe(authority.observation_plan)
    except DecisionProviderObservationFailure as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    if (
        not isinstance(remote, dict)
        or remote.get("success") is not True
        or str(remote.get("provider") or "").strip().lower() != identity.provider
        or str(remote.get("provider_target_id") or "").strip()
        != identity.provider_target_id
    ):
        raise HTTPException(
            status_code=502,
            detail=(
                "The decision ATS observation did not prove the exact provider target"
            ),
        )

    app, current = _lock_authorized_reconciliation(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
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
        lock_role_for_update=False,
        allow_globally_advanced=True,
    )
    receipt, location = _locate(app, identity)
    if location != "current":
        raise _conflict("Archived decision evidence cannot alter current local state")
    snapshot = _snapshot(receipt)
    if acting_role_id is not None and int(acting_role_id) != int(
        snapshot.acting_role_id or 0
    ):
        raise _conflict(
            "The reconciliation role does not match the exact decision receipt"
        )
    observation = receipt.get("reconciliation_observation")
    if not isinstance(observation, dict) or str(observation.get("observation_id") or "") != observation_id:
        raise _conflict("That exact decision ATS observation is no longer current")
    if str(receipt.get("status") or "").lower() == "confirmed":
        _reauthorize_locked_application(
            db,
            app=app,
            current_user=current_user,
            acting_role_id=acting_role_id,
        )
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
    authority = _lock_authority(
        db,
        app=app,
        identity=identity,
        acting_role_id=acting_role_id,
    )
    _reauthorize_locked_application(
        db,
        app=app,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
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
        allow_globally_advanced=True,
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
