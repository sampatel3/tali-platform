"""Exact read-and-resolve workflow for ambiguous ATS stage moves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.user import User
from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .ats_stage_move_claim import StageMoveClaim
from .ats_stage_move_dispatch_state import plan_stage_move_dispatch
from .ats_stage_move_finalization import finalize_stage_move_success
from .ats_stage_move_provider import (
    StageMoveObservationFailure,
    StageMoveObservationPlan,
)
from .ats_stage_move_reconciliation_context import (
    StageReceiptIdentity,
    lock_stage_reconciliation_context,
)
from .ats_stage_move_reconciliation_history import (
    ArchivedStageMoveHistoryError,
    replace_archived_stage_move_receipt,
)
from .ats_stage_move_reconciliation_retry import build_stage_move_retry_payload
from .ats_stage_move_receipt import (
    STAGE_MOVE_HISTORY_KEY,
    STAGE_MOVE_OPERATION_KEY,
    StageMoveSnapshot,
)
from .document_service import sanitize_json_for_storage
from .reconciliation_history import (
    append_reconciliation_history_or_conflict,
    require_reconciliation_history_capacity_or_conflict,
)

_OBSERVATION_MAX_AGE = timedelta(minutes=5)
_UNRESOLVED = frozenset(
    {
        "provider_call_started",
        "provider_succeeded",
        "manual_reconciliation_required",
        "retry_authorized",
        "reconciliation_observed",
    }
)


@dataclass(frozen=True)
class _CheckSnapshot:
    snapshot: StageMoveSnapshot
    receipt: dict[str, Any]
    location: str
    source_status: str
    source_updated_at: str
    observation_plan: StageMoveObservationPlan


StageObservation = Callable[[StageMoveObservationPlan], dict[str, Any]]


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_status(receipt: dict[str, Any]) -> str:
    status = str(receipt.get("status") or "").strip().lower()
    return (
        str(receipt.get("observed_receipt_status") or "").strip().lower()
        if status == "reconciliation_observed"
        else status
    )


def _require_unresolved(receipt: dict[str, Any]) -> None:
    if _source_status(receipt) not in _UNRESOLVED and not (
        receipt.get("provider_outcome_uncertain") is True
        or receipt.get("manual_reconciliation_required") is True
    ):
        raise _conflict("This exact ATS stage move does not require reconciliation")


def _validate_check_receipt(receipt: dict[str, Any], _location: str) -> None:
    _require_unresolved(receipt)
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_observation_history"
    )
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_resolution_history"
    )


def _check_snapshot(
    db: Session,
    *,
    application_id: int,
    identity: StageReceiptIdentity,
    current_user: User,
    acting_role_id: int | None,
) -> _CheckSnapshot:
    locked = lock_stage_reconciliation_context(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
        validate_receipt=_validate_check_receipt,
    )
    return _CheckSnapshot(
        snapshot=locked.snapshot,
        receipt=locked.receipt,
        location=locked.location,
        source_status=_source_status(locked.receipt),
        source_updated_at=str(locked.receipt.get("updated_at") or ""),
        observation_plan=locked.observation_plan,
    )


def _expected_stage(snap: StageMoveSnapshot) -> str:
    return str(
        snap.target_stage if snap.provider == "workable" else snap.provider_remote_stage or ""
    ).strip()


def _matches_expected(remote: dict[str, Any], expected: str) -> bool:
    values = remote.get("provider_remote_stage_values")
    values = values if isinstance(values, list) else [remote.get("provider_remote_stage")]
    return expected.casefold() in {
        str(value or "").strip().casefold() for value in values if str(value or "").strip()
    }


def check_stage_move_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: StageReceiptIdentity,
    current_user: User,
    acting_role_id: int | None = None,
    observe: StageObservation | None = None,
) -> dict[str, Any]:
    """Read an exact stage outside all DB transactions and append the evidence."""

    checked = _check_snapshot(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    db.commit()
    if db.in_transaction():
        raise RuntimeError("ATS stage observation cannot run inside a DB transaction")
    if observe is None:
        from .ats_stage_move_provider import perform_stage_move_provider_observation

        observe = perform_stage_move_provider_observation
    try:
        remote = observe(checked.observation_plan)
    except StageMoveObservationFailure as exc:
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
            detail="The ATS stage observation did not prove the exact provider target",
        )
    def validate_unchanged(receipt: dict[str, Any], location: str) -> None:
        if (
            receipt != checked.receipt
            or location != checked.location
            or _source_status(receipt) != checked.source_status
            or str(receipt.get("updated_at") or "") != checked.source_updated_at
            or str(receipt.get("snapshot_fingerprint") or "")
            != str(checked.receipt.get("snapshot_fingerprint") or "")
        ):
            raise _conflict("The exact stage-move receipt changed during the ATS check")

    locked = lock_stage_reconciliation_context(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
        validate_receipt=validate_unchanged,
    )
    app = locked.application
    receipt = locked.receipt
    location = locked.location
    snap = locked.snapshot
    stored_receipt = dict(receipt)
    checked_at = _now().isoformat()
    expected = _expected_stage(snap)
    observation = sanitize_json_for_storage(
        {
            **remote,
            "observation_id": uuid4().hex,
            "receipt_key": STAGE_MOVE_OPERATION_KEY,
            "operation_id": identity.operation_id,
            "expected_remote_stage": expected,
            "remote_matches_expected": _matches_expected(remote, expected),
            "checked_at": checked_at,
            "checked_by_actor_id": int(current_user.id),
            "observed_receipt_status": checked.source_status,
            "observed_receipt_updated_at": checked.source_updated_at,
            "snapshot_fingerprint": snap.operation_fingerprint(),
        }
    )
    receipt["reconciliation_observation"] = observation
    receipt["reconciliation_last_checked_at"] = checked_at
    append_reconciliation_history_or_conflict(
        receipt,
        history_key="reconciliation_observation_history",
        entry=observation,
        saturated_at=checked_at,
    )
    state = dict(app.integration_sync_state or {})
    if location == "current":
        state[STAGE_MOVE_OPERATION_KEY] = receipt
    else:
        evidence = {
            **receipt,
            "status": "reconciliation_observed",
            "observed_receipt_status": checked.source_status,
            "updated_at": checked_at,
        }
        try:
            state[STAGE_MOVE_HISTORY_KEY] = replace_archived_stage_move_receipt(
                state, expected=stored_receipt, replacement=evidence
            )
        except ArchivedStageMoveHistoryError as exc:
            raise _conflict(str(exc)) from None
    app.integration_sync_state = sanitize_json_for_storage(state)
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_stage_move_reconciliation_observed",
        actor_type="recruiter",
        actor_id=int(current_user.id),
        reason="Recruiter checked the exact unresolved ATS stage move",
        metadata=observation,
        idempotency_key=f"stage-observation:{observation['observation_id']}"[:200],
    )
    db.commit()
    return observation


def _append_resolution(
    db: Session,
    *,
    app: CandidateApplication,
    receipt: dict[str, Any],
    resolution: dict[str, Any],
) -> None:
    receipt["reconciliation_resolution"] = resolution
    append_reconciliation_history_or_conflict(
        receipt,
        history_key="reconciliation_resolution_history",
        entry=resolution,
        saturated_at=str(resolution["resolved_at"]),
    )
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    append_application_event(
        db,
        app=app,
        event_type="ats_stage_move_reconciliation_resolved",
        actor_type="recruiter",
        actor_id=resolution["resolved_by_actor_id"],
        reason="Recruiter resolved an exact ATS stage observation",
        metadata=resolution,
        idempotency_key=(
            f"stage-resolution:{resolution['operation_id']}:"
            f"{resolution['observation_id']}:{resolution['disposition']}"
        )[:200],
    )


def resolve_stage_move_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: StageReceiptIdentity,
    observation_id: str,
    disposition: str,
    current_user: User,
    acting_role_id: int | None = None,
) -> dict[str, Any]:
    """Finalize a proven move, or authorize one explicit observed-safe retry."""

    if disposition not in {"confirm_stage_move", "retry_stage_move"}:
        raise HTTPException(status_code=422, detail="Unsupported stage resolution")

    def validate_resolution_receipt(
        receipt: dict[str, Any], location: str
    ) -> dict[str, Any]:
        if location != "current":
            raise _conflict(
                "This archived stage move is preserved for inspection but cannot overwrite a newer operation"
            )
        require_reconciliation_history_capacity_or_conflict(
            receipt, "reconciliation_resolution_history"
        )
        observation = receipt.get("reconciliation_observation")
        if not isinstance(observation, dict) or str(
            observation.get("observation_id") or ""
        ) != observation_id:
            raise _conflict("That exact ATS stage observation is no longer current")
        if any(
            str(observation.get(key) or "") != value
            for key, value in (
                ("operation_id", identity.operation_id),
                ("provider", identity.provider),
                ("provider_target_id", identity.provider_target_id),
                (
                    "snapshot_fingerprint",
                    str(receipt.get("snapshot_fingerprint") or ""),
                ),
            )
        ):
            raise _conflict("The ATS stage observation does not match this receipt")
        try:
            checked_at = datetime.fromisoformat(
                str(observation.get("checked_at") or "")
            )
        except ValueError:
            raise _conflict("The ATS stage observation timestamp is invalid") from None
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if _now() - checked_at.astimezone(timezone.utc) > _OBSERVATION_MAX_AGE:
            raise _conflict("The ATS stage observation is stale; check it again")
        return observation

    locked = lock_stage_reconciliation_context(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
        validate_receipt=validate_resolution_receipt,
    )
    app = locked.application
    receipt = locked.receipt
    snap = locked.snapshot
    observation = locked.validation
    expected = _expected_stage(snap)
    matches = bool(observation.get("remote_matches_expected")) and _matches_expected(
        observation, expected
    )
    resolution = {
        "observation_id": observation_id,
        "operation_id": identity.operation_id,
        "receipt_key": STAGE_MOVE_OPERATION_KEY,
        "provider": identity.provider,
        "provider_target_id": identity.provider_target_id,
        "expected_remote_stage": expected,
        "observed_remote_stage": observation.get("provider_remote_stage"),
        "disposition": disposition,
        "resolved_at": _now().isoformat(),
        "resolved_by_actor_id": int(current_user.id),
    }
    if disposition == "confirm_stage_move":
        if not matches:
            raise _conflict("The ATS is not at the exact expected stage; it cannot be confirmed")
        receipt.update(
            status="provider_succeeded",
            provider_called=True,
            provider_succeeded=True,
            provider_outcome_uncertain=False,
            manual_reconciliation_required=False,
            provider_remote_stage=observation.get("provider_remote_stage"),
            provider_result={
                "success": True,
                "code": "reconciled_observation",
                "provider_remote_stage": observation.get("provider_remote_stage"),
            },
            updated_at=resolution["resolved_at"],
        )
        _append_resolution(db, app=app, receipt=receipt, resolution=resolution)
        state = dict(app.integration_sync_state or {})
        state[STAGE_MOVE_OPERATION_KEY] = receipt
        app.integration_sync_state = sanitize_json_for_storage(state)
        db.flush()
        claim = StageMoveClaim(
            snapshot=snap,
            operation_id=identity.operation_id,
            disposition="finalize_provider_success",
            provider_plan=None,
            receipt=receipt,
        )
        finalized = finalize_stage_move_success(
            db,
            claim=claim,
            provider_result=dict(receipt["provider_result"]),
        )
        from .ats_stage_move_lifecycle import queue_stage_move_related_note

        queue_stage_move_related_note(db, claim=claim, note=finalized.related_note)
        return {**finalized.result, "reconciliation": resolution}

    if matches:
        raise _conflict("The ATS already matches; confirm the completed move instead")
    try:
        require_no_other_unresolved_provider_operation(
            app,
            receipt_key=STAGE_MOVE_OPERATION_KEY,
            operation_id=identity.operation_id,
        )
    except UnresolvedProviderOperation as exc:
        raise _conflict(str(exc)) from None
    receipt.update(
        status="retry_authorized",
        provider_succeeded=False,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        reconciliation_retry_observation_id=observation_id,
        reconciliation_retry_authorized_by_actor_id=int(current_user.id),
        reconciliation_retry_authorized_at=resolution["resolved_at"],
        updated_at=resolution["resolved_at"],
    )
    _append_resolution(db, app=app, receipt=receipt, resolution=resolution)
    state = dict(app.integration_sync_state or {})
    state[STAGE_MOVE_OPERATION_KEY] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    dispatch = plan_stage_move_dispatch(
        db,
        app=app,
        organization_id=int(app.organization_id),
        operation_id=identity.operation_id,
    )
    if dispatch.action != "enqueue" or not dispatch.dispatch_key:
        raise _conflict("The exact stage retry is already queued or cannot be safely published")
    payload = build_stage_move_retry_payload(receipt, int(current_user.id))
    db.commit()
    from .workable_op_runner import OP_MOVE_STAGE, enqueue_workable_op

    try:
        job_run_id = enqueue_workable_op(
            organization_id=int(current_user.organization_id),
            op_type=OP_MOVE_STAGE,
            payload=payload,
            scope_id=int(application_id),
            dispatch_key=dispatch.dispatch_key,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The exact stage retry remains authorized but could not be durably queued. "
                "No new provider write was sent; try resolving it again."
            ),
        ) from exc
    return {
        "status": "queued",
        "application_id": int(application_id),
        "operation_id": identity.operation_id,
        "job_run_id": int(job_run_id),
        "reconciliation": resolution,
    }


__all__ = [
    "StageReceiptIdentity",
    "check_stage_move_reconciliation",
    "resolve_stage_move_reconciliation",
]
