"""Lifecycle fencing for replayable manual ATS outcome writes."""

from __future__ import annotations

from typing import Any, Callable, NoReturn

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_writeback_state import (
    OUTCOME_WRITEBACK_KEY,
    OUTCOME_WRITEBACK_RECONCILIATION_KEY,
    set_outcome_writeback_state,
)
from .application_lifecycle_restore import (
    lock_application_outcome_snapshot,
    receipt_blocks_lifecycle_restore,
)
from .document_service import sanitize_json_for_storage
from .manual_outcome_identity import (
    build_manual_outcome_operation_id,
    manual_outcome_provider_snapshot as _provider_snapshot,
    validate_manual_outcome_payload,
)

_PROVIDER_NOT_CALLED_CODES = frozenset(
    {
        "missing_actor_member_id", "missing_candidate_id", "missing_connection",
        "missing_submission_id", "missing_write_scope", "needs_mapping",
        "not_configured", "not_linked", "not_writeable", "writeback_disabled",
    }
)
_INFER_PROVIDER_CALLED = object()


def raise_manual_outcome_unavailable() -> NoReturn:
    from .workable_actions_service import WorkableWritebackError
    raise WorkableWritebackError(
        action="manual_outcome",
        code="not_linked",
        message="The application is no longer linked to its ATS provider",
        retriable=False,
    )


def require_manual_outcome_delivery(result: dict | None) -> dict:
    if result is None or str(result.get("status") or "") == "skipped":
        raise_manual_outcome_unavailable()
    return result


def manual_outcome_matches_application(
    app: CandidateApplication, payload: dict
) -> bool:
    """Return whether this operation still belongs to the live application."""
    try:
        expected_version, target_outcome, expected_outcome, _operation_id = (
            validate_manual_outcome_payload(payload)
        )
    except ValueError:
        return False
    provider, provider_target_id = _provider_snapshot(payload)
    current_target = (
        app.workable_candidate_id
        if provider == "workable"
        else app.bullhorn_job_submission_id
    )
    org = getattr(app, "organization", None)
    org_mode = str(getattr(org, "sync_mode", "") or "").strip().lower()
    provider_still_active = (
        provider == "workable" or not app.workable_candidate_id
        or org_mode == "bullhorn_primary"
    )
    return bool(
        app.deleted_at is None
        and int(app.version or 0) == expected_version
        and str(app.application_outcome or "").strip().lower() == expected_outcome
        and expected_outcome == target_outcome
        and provider_still_active
        and str(current_target or "").strip() == provider_target_id
    )


def _mark_matching_receipt_superseded(
    app: CandidateApplication, payload: dict
) -> None:
    state = app.integration_sync_state
    receipt: dict[str, Any] | None = (
        state.get(OUTCOME_WRITEBACK_KEY) if isinstance(state, dict) else None
    )
    if not isinstance(receipt, dict) or receipt.get("status") != "queued":
        return
    try:
        expected_version, target_outcome, expected_outcome, operation_id = (
            validate_manual_outcome_payload(payload)
        )
        provider, provider_target_id = _provider_snapshot(payload)
        receipt_version = int(receipt.get("expected_application_version"))
    except (TypeError, ValueError):
        return
    if (
        receipt_version != expected_version
        or str(receipt.get("target_outcome") or "").strip().lower()
        != target_outcome
        or str(receipt.get("expected_local_outcome") or "").strip().lower()
        != expected_outcome
        or str(receipt.get("operation_id") or "") != operation_id
        or str(receipt.get("provider") or "").strip().lower() != provider
        or str(receipt.get("provider_target_id") or "").strip()
        != provider_target_id
    ):
        return
    set_outcome_writeback_state(
        app,
        provider=str(receipt.get("provider") or "ats"),
        status="superseded",
        target_outcome=target_outcome,
        expected_application_version=expected_version,
        expected_local_outcome=expected_outcome,
        operation_id=operation_id,
        provider_target_id=provider_target_id,
    )


def _matching_receipt(
    app: CandidateApplication, payload: dict
) -> tuple[dict[str, Any] | None, tuple[int, str, str, str] | None]:
    try:
        snapshot = validate_manual_outcome_payload(payload)
    except ValueError:
        return None, None
    expected_version, target_outcome, expected_outcome, operation_id = snapshot
    provider, provider_target_id = _provider_snapshot(payload)
    state = app.integration_sync_state
    receipt = state.get(OUTCOME_WRITEBACK_KEY) if isinstance(state, dict) else None
    if not isinstance(receipt, dict):
        return None, snapshot
    try:
        matches = (
            int(receipt.get("application_id")) == int(app.id)
            and int(receipt.get("expected_application_version")) == expected_version
            and str(receipt.get("target_outcome") or "").strip().lower()
            == target_outcome
            and str(receipt.get("expected_local_outcome") or "").strip().lower()
            == expected_outcome
            and str(receipt.get("operation_id") or "") == operation_id
            and str(receipt.get("provider") or "").strip().lower() == provider
            and str(receipt.get("provider_target_id") or "").strip()
            == provider_target_id
        )
    except (TypeError, ValueError):
        matches = False
    return (dict(receipt) if matches else None), snapshot


def _surface_uncertain_prior_attempt(
    db: Session,
    app: CandidateApplication,
    receipt: dict[str, Any],
    snapshot: tuple[int, str, str, str],
) -> dict[str, Any]:
    from ..domains.assessments_runtime.pipeline_service import append_application_event
    expected_version, target_outcome, expected_outcome, operation_id = snapshot
    provider = str(receipt.get("provider") or "ats")
    _provider, provider_target_id = _provider_snapshot(
        {
            "provider": provider,
            "provider_target_id": receipt.get("provider_target_id"),
        }
    )
    reason = (
        "A prior ATS outcome attempt crossed the provider boundary without a "
        "provable result. Verify the ATS status before restoring or retrying."
    )
    set_outcome_writeback_state(
        app,
        provider=provider,
        status="manual_reconciliation_required",
        target_outcome=target_outcome,
        expected_application_version=expected_version,
        expected_local_outcome=expected_outcome,
        provider_outcome_uncertain=True,
        operation_id=operation_id,
        provider_target_id=provider_target_id,
    )
    append_application_event(
        db,
        app=app,
        event_type="ats_outcome_writeback_manual_reconciliation_required",
        actor_type="system",
        reason=reason,
        metadata={"ats": provider, "target_outcome": target_outcome},
        idempotency_key=f"manual-outcome-reconcile:{app.id}:{expected_version}",
    )
    db.commit()
    return {
        "status": "manual_reconciliation_required",
        "reason": reason,
        "application_id": int(app.id),
        "failed": 1,
    }


def _record_orphaned_reconciliation(
    db: Session,
    app: CandidateApplication,
    payload: dict,
    *,
    provider: str,
    provider_called: bool | None,
    provider_succeeded: bool | None,
    reason: str,
) -> dict[str, Any]:
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    expected_version, target_outcome, _expected, operation_id = (
        validate_manual_outcome_payload(payload)
    )
    _payload_provider, provider_target_id = _provider_snapshot(payload)
    needs_reconciliation = provider_succeeded is not False
    state = (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    matching_receipt, _snapshot = _matching_receipt(app, payload)
    receipt_key = (
        OUTCOME_WRITEBACK_KEY
        if matching_receipt is not None
        else OUTCOME_WRITEBACK_RECONCILIATION_KEY
    )
    state[receipt_key] = {
        **(matching_receipt or {}),
        "application_id": int(app.id),
        "operation_id": operation_id,
        "status": (
            "manual_reconciliation_required" if needs_reconciliation else "failed"
        ),
        "provider": provider,
        "provider_target_id": provider_target_id,
        "target_outcome": target_outcome,
        "expected_application_version": expected_version,
        "observed_application_version": int(app.version or 0),
        "observed_application_outcome": str(app.application_outcome or "").lower(),
        "provider_called": provider_called,
        "provider_succeeded": provider_succeeded,
        "provider_outcome_uncertain": provider_succeeded is None,
        "manual_reconciliation_required": needs_reconciliation,
    }
    app.integration_sync_state = sanitize_json_for_storage(state)
    append_application_event(
        db,
        app=app,
        event_type=(
            "ats_outcome_writeback_manual_reconciliation_required"
            if needs_reconciliation
            else f"{provider}_writeback_failed"
        ),
        actor_type="system",
        reason=reason,
        metadata={
            "operation_id": operation_id,
            "ats": provider,
            "provider_succeeded": provider_succeeded,
            "local_outcome_preserved": app.application_outcome,
        },
        idempotency_key=f"{operation_id}:orphaned-reconciliation"[:200],
    )
    db.commit()
    return {
        "status": (
            "manual_reconciliation_required" if needs_reconciliation else "failed"
        ),
        "application_id": int(app.id),
        "reason": reason,
        "failed": 1,
    }


def finalize_manual_outcome_success(
    db: Session,
    app: CandidateApplication,
    payload: dict,
    *,
    provider: str,
    remote_status: str | None = None,
    on_exact_success: Callable[[CandidateApplication], None] | None = None,
) -> dict[str, Any] | None:
    """Confirm the exact claim or preserve a newer receipt and surface drift."""
    lock_application_outcome_snapshot(db, app)
    receipt, snapshot = _matching_receipt(app, payload)
    if (
        receipt is not None
        and snapshot is not None
        and manual_outcome_matches_application(app, payload)
    ):
        status = str(receipt.get("status") or "").strip().lower()
        if status == "confirmed":
            return None
        if status == "provider_call_started":
            expected_version, target_outcome, expected_outcome, operation_id = snapshot
            _provider, provider_target_id = _provider_snapshot(payload)
            if on_exact_success is not None:
                on_exact_success(app)
            set_outcome_writeback_state(
                app,
                provider=provider,
                status="confirmed",
                target_outcome=target_outcome,
                expected_application_version=expected_version,
                expected_local_outcome=expected_outcome,
                operation_id=operation_id,
                provider_target_id=provider_target_id,
                remote_status=remote_status,
            )
            return None
    return _record_orphaned_reconciliation(
        db,
        app,
        payload,
        provider=provider,
        provider_called=True,
        provider_succeeded=True,
        reason=(
            f"{provider.title()} confirmed an older outcome after the local "
            "application changed. The newer local outcome was preserved; "
            "verify the ATS status before continuing."
        ),
    )


def surface_manual_outcome_failure(
    db: Session,
    app: CandidateApplication,
    payload: dict,
    *,
    error_code: str,
    error_message: str,
    provider_called: bool | None | object = _INFER_PROVIDER_CALLED,
) -> bool:
    """Terminalize the exact claim without inventing provider certainty."""

    from ..domains.assessments_runtime.pipeline_service import append_application_event

    lock_application_outcome_snapshot(db, app)
    receipt, snapshot = _matching_receipt(app, payload)
    if (
        receipt is None
        or snapshot is None
        or not manual_outcome_matches_application(app, payload)
    ):
        payload_provider, _provider_target_id = _provider_snapshot(payload)
        effective_provider_called = (
            provider_called
            if provider_called is not _INFER_PROVIDER_CALLED
            else (
                False
                if str(error_code or "") in _PROVIDER_NOT_CALLED_CODES
                else None
            )
        )
        definitely_not_called = effective_provider_called is False
        _record_orphaned_reconciliation(
            db,
            app,
            payload,
            provider=payload_provider,
            provider_called=effective_provider_called,
            provider_succeeded=False if definitely_not_called else None,
            reason=(
                "An older ATS outcome attempt failed after the application changed. "
                + (
                    "The provider was not called."
                    if definitely_not_called
                    else "Its provider result is uncertain; verify it manually."
                )
            ),
        )
        return True
    if str(receipt.get("status") or "") != "provider_call_started":
        return False
    expected_version, target_outcome, expected_outcome, operation_id = snapshot
    provider = str(receipt.get("provider") or "ats")
    _provider, provider_target_id = _provider_snapshot(payload)
    outcome_uncertain = (
        provider_called is None
        if provider_called is not _INFER_PROVIDER_CALLED
        else str(error_code or "") not in _PROVIDER_NOT_CALLED_CODES
    )
    status = "manual_reconciliation_required" if outcome_uncertain else "failed"
    set_outcome_writeback_state(
        app,
        provider=provider,
        status=status,
        target_outcome=target_outcome,
        error_code=error_code,
        expected_application_version=expected_version,
        expected_local_outcome=expected_outcome,
        provider_outcome_uncertain=outcome_uncertain,
        operation_id=operation_id,
        provider_target_id=provider_target_id,
    )
    label = "Bullhorn" if provider == "bullhorn" else "Workable"
    reason = (
        f"{label} outcome is uncertain; verify it before restoring this application."
        if outcome_uncertain
        else f"{label} did not accept the outcome update. {error_message}"
    )
    append_application_event(
        db,
        app=app,
        event_type=(
            "ats_outcome_writeback_manual_reconciliation_required"
            if outcome_uncertain
            else f"{provider}_writeback_failed"
        ),
        actor_type="system",
        reason=reason,
        metadata={"op_type": "manual_outcome", "code": error_code, "ats": provider},
        idempotency_key=(
            f"manual-outcome-failure:{app.id}:{expected_version}:{error_code}"[:200]
        ),
    )
    db.commit()
    return True


def preflight_manual_outcome(
    db: Session, organization_id: int, payload: dict
) -> dict[str, Any] | None:
    """Fence a stale replay, releasing the application lock before ATS I/O."""
    application_id = int(payload["application_id"])
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == int(organization_id),
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    receipt, snapshot = _matching_receipt(app, payload) if app is not None else (None, None)
    if (
        app is not None
        and snapshot is not None
        and receipt is not None
        and manual_outcome_matches_application(app, payload)
    ):
        status = str(receipt.get("status") or "").strip().lower()
        if status == "confirmed":
            db.rollback()
            return {
                "status": "already_completed",
                "application_id": application_id,
            }
        if status != "queued" or receipt_blocks_lifecycle_restore(
            receipt, receipt_key=OUTCOME_WRITEBACK_KEY
        ):
            if receipt_blocks_lifecycle_restore(
                receipt, receipt_key=OUTCOME_WRITEBACK_KEY
            ):
                return _surface_uncertain_prior_attempt(db, app, receipt, snapshot)
            db.rollback()
            return {
                "status": "superseded",
                "reason": f"operation_{status or 'inactive'}",
                "application_id": application_id,
            }
        expected_version, target_outcome, expected_outcome, operation_id = snapshot
        _provider, provider_target_id = _provider_snapshot(payload)
        set_outcome_writeback_state(
            app,
            provider=str(receipt.get("provider") or "ats"),
            status="provider_call_started",
            target_outcome=target_outcome,
            expected_application_version=expected_version,
            expected_local_outcome=expected_outcome,
            operation_id=operation_id,
            provider_target_id=provider_target_id,
        )
        db.commit()  # Persist the claim and release the row before provider I/O.
        return None
    if app is not None:
        _mark_matching_receipt_superseded(app, payload)
        db.commit()
    else:
        db.rollback()
    return {
        "status": "superseded",
        "reason": "application_lifecycle_changed",
        "application_id": application_id,
    }

__all__ = [
    "build_manual_outcome_operation_id", "finalize_manual_outcome_success",
    "manual_outcome_matches_application", "preflight_manual_outcome",
    "raise_manual_outcome_unavailable", "require_manual_outcome_delivery",
    "surface_manual_outcome_failure", "validate_manual_outcome_payload",
]
