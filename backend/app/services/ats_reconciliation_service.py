"""Evidence-backed recovery for unresolved ATS operation receipts.

The provider read deliberately sits between two short application row locks.
An observation can only resolve the exact receipt it inspected, and every
observation/resolution is retained both on the receipt and in the immutable
application event stream.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    transition_outcome,
)
from ..models.candidate_application import CandidateApplication
from ..models.user import User
from .application_lifecycle_restore import receipt_blocks_lifecycle_restore
from .ats_reconciliation_authority import lock_reconciliation_application
from .ats_reconciliation_evidence import (
    RECONCILIATION_DISPOSITIONS,
    has_exact_reconciliation_resolution,
)
from .ats_reconciliation_provider import read_provider_observation
from .ats_reconciliation_types import ProviderLookup, ReceiptIdentity, ReceiptSnapshot
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .reconciliation_history import (
    RECONCILIATION_HISTORY_SATURATION_KEY,
    append_reconciliation_history_or_conflict,
    require_reconciliation_history_capacity_or_conflict,
)

RECONCILABLE_RECEIPT_KEYS = frozenset(
    {
        "auto_reject_operation",
        "cv_gap_rejection_operation",
        "outcome_writeback",
        "outcome_writeback_reconciliation",
    }
)
OBSERVATION_MAX_AGE = timedelta(minutes=5)

_RECONCILIATION_FIELDS = frozenset(
    {
        "reconciliation_status",
        "reconciliation_resolved_at",
        "provider_reconciled_at",
        "resolved_operation_id",
        "reconciliation_resolved_by_actor_id",
        "reconciliation_resolved_by_actor_type",
        "reconciliation_evidence",
        "reconciliation_observation_id",
        "reconciliation_disposition",
        "reconciliation_observation",
        "reconciliation_observation_history",
        "reconciliation_resolution_history",
        "reconciliation_last_checked_at",
        RECONCILIATION_HISTORY_SATURATION_KEY,
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _http_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _receipt_fingerprint(receipt: dict[str, Any]) -> str:
    provider_phase = {
        key: value for key, value in receipt.items() if key not in _RECONCILIATION_FIELDS
    }
    canonical = json.dumps(
        sanitize_json_for_storage(provider_phase),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identity_from_receipt(
    receipt_key: str, receipt: dict[str, Any]
) -> ReceiptIdentity:
    return ReceiptIdentity(
        receipt_key=receipt_key,
        operation_id=str(receipt.get("operation_id") or "").strip(),
        provider=_normalized(receipt.get("provider")),
        provider_target_id=str(receipt.get("provider_target_id") or "").strip(),
    )


def _require_requested_identity(
    receipt: dict[str, Any], requested: ReceiptIdentity
) -> None:
    actual = _identity_from_receipt(requested.receipt_key, receipt)
    if not actual.operation_id or not actual.provider or not actual.provider_target_id:
        raise _http_conflict(
            "This legacy receipt lacks exact ATS operation identity. Refresh or "
            "escalate it for support-assisted reconciliation."
        )
    if actual != requested:
        raise _http_conflict(
            "The ATS operation receipt changed. Refresh the candidate before checking it."
        )


def _receipt_needs_reconciliation(receipt: dict[str, Any], receipt_key: str) -> bool:
    if has_exact_reconciliation_resolution(receipt, receipt_key=receipt_key):
        return False
    status = _normalized(receipt.get("status"))
    return bool(
        status in {
            "provider_call_started",
            "provider_succeeded",
            "manual_reconciliation_required",
        }
        or receipt.get("manual_reconciliation_required") is True
        or receipt.get("provider_outcome_uncertain") is True
        or (receipt.get("provider_succeeded") is True and status not in {"completed", "confirmed"})
    )


def _receipt_for_identity(
    app: CandidateApplication, identity: ReceiptIdentity
) -> dict[str, Any]:
    if identity.receipt_key not in RECONCILABLE_RECEIPT_KEYS:
        raise HTTPException(status_code=422, detail="Unsupported ATS receipt family")
    state = app.integration_sync_state
    receipt = state.get(identity.receipt_key) if isinstance(state, dict) else None
    if not isinstance(receipt, dict):
        raise HTTPException(status_code=404, detail="ATS operation receipt not found")
    receipt = dict(receipt)
    _require_requested_identity(receipt, identity)
    if has_exact_reconciliation_resolution(
        receipt, receipt_key=identity.receipt_key
    ):
        raise _http_conflict("This exact ATS operation has already been reconciled.")
    if not _receipt_needs_reconciliation(receipt, identity.receipt_key):
        raise _http_conflict("This ATS operation does not require reconciliation.")
    return receipt


def _snapshot_for_check(
    db: Session,
    *,
    application_id: int,
    identity: ReceiptIdentity,
    current_user: User,
    acting_role_id: int | None,
) -> ReceiptSnapshot:
    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    receipt = _receipt_for_identity(app, identity)
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_observation_history"
    )
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_resolution_history"
    )
    return ReceiptSnapshot(
        application_id=int(app.id),
        organization_id=int(app.organization_id),
        application_version=int(app.version or 1),
        application_outcome=_normalized(app.application_outcome) or "open",
        identity=identity,
        receipt_fingerprint=_receipt_fingerprint(receipt),
    )


def _brief_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("kind") or value.get("slug") or value.get("name")
    return sanitize_text_for_storage(str(value or "").strip())[:200]


def check_ats_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: ReceiptIdentity,
    current_user: User,
    acting_role_id: int | None = None,
    provider_lookup: ProviderLookup = read_provider_observation,
) -> dict[str, Any]:
    """Validate, unlock for one remote read, then persist an exact observation."""

    snapshot = _snapshot_for_check(
        db,
        application_id=application_id,
        identity=identity,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    db.commit()  # Release application/role locks before provider network I/O.
    try:
        remote = provider_lookup(db, snapshot)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"Could not read the exact ATS target: {type(exc).__name__}",
        ) from None

    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    receipt = _receipt_for_identity(app, identity)
    if _receipt_fingerprint(receipt) != snapshot.receipt_fingerprint:
        db.rollback()
        raise _http_conflict(
            "The ATS operation changed while its status was checked. Refresh and check again."
        )
    if (
        int(app.version or 1) != snapshot.application_version
        or _normalized(app.application_outcome) != snapshot.application_outcome
    ):
        db.rollback()
        raise _http_conflict(
            "The application changed while its ATS status was checked. Refresh and check again."
        )

    checked_at = _now().isoformat()
    observation_id = uuid4().hex
    observation = sanitize_json_for_storage(
        {
            "observation_id": observation_id,
            "receipt_key": identity.receipt_key,
            "operation_id": identity.operation_id,
            "provider": identity.provider,
            "provider_target_id": identity.provider_target_id,
            "remote_outcome": _normalized(remote.get("remote_outcome")) or "unknown",
            "remote_status": _brief_text(remote.get("remote_status")),
            "evidence": remote.get("evidence") if isinstance(remote.get("evidence"), dict) else {},
            "checked_at": checked_at,
            "checked_by_actor_id": int(current_user.id),
            "checked_by_actor_type": "recruiter",
            "observed_application_version": int(app.version or 1),
            "observed_application_outcome": _normalized(app.application_outcome),
            "receipt_fingerprint": snapshot.receipt_fingerprint,
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
    state[identity.receipt_key] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    append_application_event(
        db,
        app=app,
        event_type="ats_reconciliation_observed",
        actor_type="recruiter",
        actor_id=int(current_user.id),
        reason="Recruiter checked the exact unresolved ATS operation",
        metadata=observation,
        idempotency_key=f"ats-reconciliation-observation:{observation_id}"[:200],
    )
    db.commit()
    return observation


def _parsed_checked_at(observation: dict[str, Any]) -> datetime:
    try:
        checked_at = datetime.fromisoformat(str(observation.get("checked_at") or ""))
    except ValueError as exc:
        raise _http_conflict("The ATS observation timestamp is invalid. Check again.") from exc
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at.astimezone(timezone.utc)


def _require_other_receipts_safe(
    app: CandidateApplication, *, resolving_key: str
) -> None:
    state = app.integration_sync_state if isinstance(app.integration_sync_state, dict) else {}
    for key in RECONCILABLE_RECEIPT_KEYS:
        if key == resolving_key:
            continue
        receipt = state.get(key)
        if isinstance(receipt, dict) and receipt_blocks_lifecycle_restore(
            receipt, receipt_key=key
        ):
            raise _http_conflict(
                f"Resolve the other unresolved ATS receipt ({key}) before aligning local state."
            )


def resolve_ats_reconciliation(
    db: Session,
    *,
    application_id: int,
    identity: ReceiptIdentity,
    observation_id: str,
    disposition: str,
    current_user: User,
    acting_role_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one explicit, evidence-backed disposition to the exact receipt."""

    if disposition not in RECONCILIATION_DISPOSITIONS:
        raise HTTPException(status_code=422, detail="Unsupported reconciliation disposition")
    app = lock_reconciliation_application(
        db,
        application_id=application_id,
        current_user=current_user,
        acting_role_id=acting_role_id,
    )
    state = dict(app.integration_sync_state or {})
    raw_receipt = state.get(identity.receipt_key)
    if not isinstance(raw_receipt, dict):
        raise HTTPException(status_code=404, detail="ATS operation receipt not found")
    receipt = dict(raw_receipt)
    _require_requested_identity(receipt, identity)
    if has_exact_reconciliation_resolution(
        receipt, receipt_key=identity.receipt_key
    ):
        if (
            str(receipt.get("reconciliation_observation_id") or "") == observation_id
            and str(receipt.get("reconciliation_disposition") or "") == disposition
        ):
            return dict(receipt.get("reconciliation_evidence") or {})
        raise _http_conflict("This exact ATS operation was already resolved differently.")
    require_reconciliation_history_capacity_or_conflict(
        receipt, "reconciliation_resolution_history"
    )

    observation = receipt.get("reconciliation_observation")
    if not isinstance(observation, dict) or str(observation.get("observation_id") or "") != observation_id:
        raise _http_conflict("That ATS observation is no longer current. Check ATS status again.")
    if any(
        str(observation.get(field) or "") != expected
        for field, expected in (
            ("receipt_key", identity.receipt_key),
            ("operation_id", identity.operation_id),
            ("provider", identity.provider),
            ("provider_target_id", identity.provider_target_id),
        )
    ):
        raise _http_conflict("The ATS observation does not match this exact receipt.")
    if str(observation.get("receipt_fingerprint") or "") != _receipt_fingerprint(receipt):
        raise _http_conflict("The operation changed after the ATS check. Check again.")
    clock = (now or _now()).astimezone(timezone.utc)
    if clock - _parsed_checked_at(observation) > OBSERVATION_MAX_AGE:
        raise _http_conflict("The ATS observation is stale. Check ATS status again.")
    if (
        int(observation.get("observed_application_version") or 0) != int(app.version or 1)
        or _normalized(observation.get("observed_application_outcome"))
        != _normalized(app.application_outcome)
    ):
        raise _http_conflict("The application changed after the ATS check. Check again.")

    remote_outcome = _normalized(observation.get("remote_outcome"))
    local_before = _normalized(app.application_outcome) or "open"
    if remote_outcome not in {"open", "rejected"}:
        raise _http_conflict(
            "The ATS status is not safely classifiable as open or rejected. Map or inspect it before resolving."
        )
    if disposition == "confirm_provider_matches_local":
        if remote_outcome != local_before:
            raise _http_conflict(
                "The ATS and Taali outcomes differ. Choose the explicit align action instead."
            )
    else:
        if remote_outcome == local_before:
            raise _http_conflict(
                "The ATS already matches Taali. Choose the confirm-match action instead."
            )
        _require_other_receipts_safe(app, resolving_key=identity.receipt_key)
        transition_outcome(
            db,
            app=app,
            to_outcome=remote_outcome,
            actor_type="recruiter",
            actor_id=int(current_user.id),
            reason="Aligned Taali to a freshly observed exact ATS operation",
            metadata={
                "receipt_key": identity.receipt_key,
                "operation_id": identity.operation_id,
                "observation_id": observation_id,
                "ats_provider": identity.provider,
                "provider_target_id": identity.provider_target_id,
            },
            idempotency_key=f"ats-reconciliation-align:{identity.receipt_key}:{identity.operation_id}"[:200],
            expected_version=int(app.version or 1),
            operation_receipt_key=identity.operation_id,
        )

    resolved_at = clock.isoformat()
    evidence = sanitize_json_for_storage(
        {
            "observation_id": observation_id,
            "receipt_key": identity.receipt_key,
            "operation_id": identity.operation_id,
            "provider": identity.provider,
            "provider_target_id": identity.provider_target_id,
            "remote_outcome": remote_outcome,
            "remote_status": observation.get("remote_status"),
            "provider_evidence": observation.get("evidence"),
            "checked_at": observation.get("checked_at"),
            "resolved_at": resolved_at,
            "local_outcome_before": local_before,
            "local_outcome_after": _normalized(app.application_outcome),
            "disposition": disposition,
        }
    )
    # Re-read after transition_outcome; its lock refresh is authoritative.
    state = dict(app.integration_sync_state or {})
    receipt = dict(state.get(identity.receipt_key) or {})
    _require_requested_identity(receipt, identity)
    receipt.update(
        reconciliation_status="resolved",
        reconciliation_resolved_at=resolved_at,
        provider_reconciled_at=resolved_at,
        resolved_operation_id=identity.operation_id,
        resolved_receipt_key=identity.receipt_key,
        reconciliation_resolved_by_actor_id=int(current_user.id),
        reconciliation_resolved_by_actor_type="recruiter",
        reconciliation_evidence=evidence,
        reconciliation_observation_id=observation_id,
        reconciliation_disposition=disposition,
    )
    append_reconciliation_history_or_conflict(
        receipt,
        history_key="reconciliation_resolution_history",
        entry=evidence,
        saturated_at=resolved_at,
    )
    state[identity.receipt_key] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    append_application_event(
        db,
        app=app,
        event_type="ats_reconciliation_resolved",
        actor_type="recruiter",
        actor_id=int(current_user.id),
        reason="Recruiter explicitly resolved an exact ATS operation observation",
        metadata=evidence,
        idempotency_key=(
            f"ats-reconciliation-resolution:{identity.receipt_key}:"
            f"{identity.operation_id}:{observation_id}:{disposition}"
        )[:200],
    )
    db.commit()
    return evidence


__all__ = [
    "OBSERVATION_MAX_AGE",
    "RECONCILABLE_RECEIPT_KEYS",
    "RECONCILIATION_DISPOSITIONS",
    "ReceiptIdentity",
    "check_ats_reconciliation",
    "read_provider_observation",
    "resolve_ats_reconciliation",
]
