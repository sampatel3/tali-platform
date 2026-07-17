"""Durable authority fence for provider-backed automatic rejection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .document_service import sanitize_json_for_storage, sanitize_text_for_storage
from .pre_screening_service import mark_auto_reject_state

AUTO_REJECT_OPERATION_KEY = "auto_reject_operation"
_ACTIVE_STATUSES = frozenset({"authorized", "provider_call_started"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_state(app: CandidateApplication) -> dict[str, Any]:
    return (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )


def auto_reject_operation_receipt(
    app: CandidateApplication,
) -> dict[str, Any] | None:
    receipt = _sync_state(app).get(AUTO_REJECT_OPERATION_KEY)
    return dict(receipt) if isinstance(receipt, dict) else None


def _write_receipt(
    app: CandidateApplication, receipt: dict[str, Any]
) -> dict[str, Any]:
    state = _sync_state(app)
    state[AUTO_REJECT_OPERATION_KEY] = dict(receipt)
    app.integration_sync_state = sanitize_json_for_storage(state)
    return receipt


def authorize_auto_reject_operation(
    *,
    app: CandidateApplication,
    organization: Organization,
    role: Role,
    decision: dict[str, Any],
    receipt_key: str | None,
) -> dict[str, Any]:
    """Persist the exact local and authority snapshot that permits ATS I/O."""

    operation_id = sanitize_text_for_storage(str(receipt_key or "").strip())
    if not operation_id:
        operation_id = f"auto-reject:{int(app.id)}:{uuid4().hex}"
    provider = str(decision.get("provider") or "").strip().lower()
    target = str(decision.get("provider_target_id") or "").strip()
    now = _now()
    receipt = {
        "operation_id": operation_id,
        "status": "authorized",
        "organization_id": int(organization.id),
        "application_id": int(app.id),
        "expected_application_outcome": str(
            app.application_outcome or "open"
        ).strip().lower(),
        "expected_application_version": int(app.version or 1),
        "role_id": int(role.id),
        "expected_role_version": int(role.version or 1),
        "expected_workspace_control_version": int(
            organization.agent_workspace_control_version or 1
        ),
        "provider": provider,
        "provider_target_id": target,
        "provider_called": False,
        "provider_succeeded": False,
        "provider_outcome_uncertain": False,
        "authorized_at": now,
        "updated_at": now,
    }
    _write_receipt(app, receipt)
    return {**decision, "operation_id": operation_id}


def auto_reject_operation_drift_reason(
    db,
    *,
    app: CandidateApplication | None,
    organization: Organization | None,
    role: Role | None,
    decision: dict[str, Any],
) -> str | None:
    """Return why the durable receipt no longer authorizes provider work."""

    if app is None or organization is None or role is None:
        return "local_authority_unavailable"
    receipt = auto_reject_operation_receipt(app)
    operation_id = str(decision.get("operation_id") or "")
    if receipt is None:
        return "operation_receipt_missing"
    if str(receipt.get("operation_id") or "") != operation_id:
        return "operation_receipt_replaced"
    if str(receipt.get("status") or "") not in _ACTIVE_STATUSES:
        return f"operation_{str(receipt.get('status') or 'inactive')}"
    if app.deleted_at is not None:
        return "application_deleted"
    current_outcome = str(app.application_outcome or "open").strip().lower()
    if current_outcome != str(
        receipt.get("expected_application_outcome") or "open"
    ):
        return "application_outcome_changed"
    if int(app.version or 1) != int(receipt.get("expected_application_version") or 0):
        return "application_version_changed"
    if int(role.id) != int(receipt.get("role_id") or 0):
        return "application_role_changed"
    if int(role.version or 1) != int(receipt.get("expected_role_version") or 0):
        return "role_version_changed"
    if int(organization.id) != int(receipt.get("organization_id") or 0):
        return "workspace_changed"
    if int(organization.agent_workspace_control_version or 1) != int(
        receipt.get("expected_workspace_control_version") or 0
    ):
        return "workspace_authority_changed"
    from .role_execution_guard import automatic_role_action_block_reason

    execution_block = automatic_role_action_block_reason(role, db=db)
    if execution_block:
        return f"automatic_authority_blocked:{execution_block}"
    if not (
        bool(getattr(role, "auto_reject", False))
        or bool(getattr(role, "auto_reject_pre_screen", False))
    ):
        return "automatic_reject_disabled"
    provider = str(receipt.get("provider") or "")
    current_target = str(
        (
            app.bullhorn_job_submission_id
            if provider == "bullhorn"
            else app.workable_candidate_id
        )
        or ""
    ).strip()
    if current_target != str(receipt.get("provider_target_id") or ""):
        return "provider_target_changed"
    return None


def mark_auto_reject_provider_call_started(
    app: CandidateApplication, *, operation_id: str
) -> None:
    receipt = auto_reject_operation_receipt(app)
    if receipt is None or str(receipt.get("operation_id") or "") != operation_id:
        return
    now = _now()
    receipt.update(
        status="provider_call_started",
        provider_call_started_at=now,
        # The durable boundary is written immediately before provider I/O. A
        # crash or transport timeout after this point cannot prove whether the
        # remote side applied the request, so do not keep the authorization's
        # earlier ``False`` values and accidentally present them as certainty.
        provider_called=None,
        provider_succeeded=None,
        provider_outcome_uncertain=True,
        updated_at=now,
    )
    _write_receipt(app, receipt)


def mark_auto_reject_terminal_failure(
    app: CandidateApplication,
    *,
    operation_id: str,
    error_code: str,
    error_message: str,
    provider_called: bool | None,
) -> dict[str, Any] | None:
    """Terminalize the matching operation without inventing provider certainty.

    ``provider_called=None`` means the worker crossed the provider-call boundary
    but the final outcome is unknown (for example, a timeout after the request
    may have reached the ATS). Only the exact active operation may be changed;
    a lifecycle restore or newer operation remains authoritative.
    """

    clean_operation_id = str(operation_id or "").strip()
    receipt = auto_reject_operation_receipt(app)
    if (
        not clean_operation_id
        or receipt is None
        or str(receipt.get("operation_id") or "") != clean_operation_id
        or str(receipt.get("status") or "") not in _ACTIVE_STATUSES
    ):
        return None
    now = _now()
    outcome_uncertain = provider_called is None
    receipt.update(
        status="failed",
        failed_at=now,
        failure_code=sanitize_text_for_storage(str(error_code or "provider_error")),
        failure_message=sanitize_text_for_storage(
            str(error_message or "ATS reject write-back failed")
        ),
        provider_called=provider_called,
        provider_succeeded=(False if provider_called is False else None),
        provider_outcome_uncertain=outcome_uncertain,
        manual_reconciliation_required=outcome_uncertain,
        observed_application_outcome=str(
            app.application_outcome or "open"
        ).strip().lower(),
        observed_application_version=int(app.version or 1),
        updated_at=now,
    )
    return _write_receipt(app, receipt)


def supersede_auto_reject_operation(
    app: CandidateApplication,
    *,
    target_outcome: str,
    actor_type: str,
    matching_operation_id: str | None = None,
) -> bool:
    """Fence an in-flight automatic reject before another outcome mutation."""

    receipt = auto_reject_operation_receipt(app)
    if receipt is None or str(receipt.get("status") or "") not in _ACTIVE_STATUSES:
        return False
    operation_id = str(receipt.get("operation_id") or "")
    if matching_operation_id and operation_id == str(matching_operation_id):
        return False
    now = _now()
    receipt.update(
        status="superseded",
        superseded_at=now,
        superseded_by_actor_type=str(actor_type or "system")[:32],
        superseded_by_target_outcome=str(target_outcome or "").strip().lower(),
        observed_application_version=int(app.version or 1),
        updated_at=now,
    )
    _write_receipt(app, receipt)
    return True


def fence_auto_reject_lifecycle_restore(
    db,
    app: CandidateApplication,
    *,
    actor_type: str,
    target_outcome: str = "open",
) -> bool:
    """Compatibility seam for the provider-neutral lifecycle restore gate."""

    from .application_lifecycle_restore import (
        LifecycleRestoreDeferred,
        fence_application_lifecycle_restore,
    )

    try:
        return fence_application_lifecycle_restore(
            db,
            app,
            target_outcome=target_outcome,
            actor_type=actor_type,
        )
    except LifecycleRestoreDeferred as exc:
        if actor_type not in {"candidate", "recruiter"}:
            raise
        from fastapi import HTTPException

        db.rollback()
        detail = exc.public_detail if actor_type == "candidate" else exc.staff_detail
        raise HTTPException(status_code=409, detail=detail) from None


def fence_auto_reject_outcome(
    db,
    app: CandidateApplication,
    target_outcome: str,
    actor_type: str,
    matching_operation_id: str | None,
    already_locked: bool = False,
) -> bool:
    """Provider-neutral canonical-outcome fence kept behind the legacy seam."""

    from .application_lifecycle_restore import (
        LifecycleOutcomeMutationDeferred,
        fence_application_outcome_mutation,
    )

    try:
        return fence_application_outcome_mutation(
            db,
            app,
            target_outcome=target_outcome,
            actor_type=actor_type,
            matching_operation_id=matching_operation_id,
            already_locked=already_locked,
        )
    except LifecycleOutcomeMutationDeferred as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=exc.detail) from None


def lock_application_outcome_for_transition(db, app: CandidateApplication) -> None:
    """Reload the canonical lifecycle before transition preconditions are read."""

    from .application_lifecycle_restore import (
        LifecycleOutcomeMutationDeferred,
        lock_application_outcome_snapshot,
    )

    try:
        lock_application_outcome_snapshot(db, app)
    except LifecycleOutcomeMutationDeferred as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=exc.detail) from None


def cancel_auto_reject_before_provider(
    db,
    *,
    app: CandidateApplication,
    decision: dict[str, Any],
    drift_reason: str,
    actor_type: str,
) -> None:
    """Record a safe cancellation when no provider side effect occurred."""

    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
    )

    operation_id = str(decision.get("operation_id") or "")
    receipt = auto_reject_operation_receipt(app) or {}
    if str(receipt.get("operation_id") or "") == operation_id:
        now = _now()
        updates = {
            "cancellation_reason": drift_reason,
            "cancelled_at": now,
            "observed_application_outcome": str(
                app.application_outcome or "open"
            ).strip().lower(),
            "observed_application_version": int(app.version or 1),
            "provider_called": False,
            "provider_succeeded": False,
            "provider_outcome_uncertain": False,
            "manual_reconciliation_required": False,
            "updated_at": now,
        }
        if str(receipt.get("status") or "") in _ACTIVE_STATUSES:
            updates["status"] = "cancelled_before_provider"
        receipt.update(updates)
        _write_receipt(app, receipt)
    reason = f"Automatic reject cancelled before ATS write-back: {drift_reason}"
    mark_auto_reject_state(app, state="cancelled", reason=reason, triggered=False)
    append_application_event(
        db,
        app=app,
        event_type="auto_reject_writeback_cancelled",
        actor_type=actor_type,
        reason=reason,
        metadata={
            "operation_id": operation_id,
            "drift_reason": drift_reason,
            "provider_called": False,
        },
        idempotency_key=(f"{operation_id}:cancelled"[:200] or None),
    )


def complete_auto_reject_operation(
    app: CandidateApplication, *, operation_id: str
) -> None:
    receipt = auto_reject_operation_receipt(app)
    if receipt is None or str(receipt.get("operation_id") or "") != operation_id:
        return
    now = _now()
    receipt.update(
        status="completed",
        completed_at=now,
        provider_called=True,
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        manual_reconciliation_required=False,
        observed_application_outcome=str(
            app.application_outcome or "open"
        ).strip().lower(),
        observed_application_version=int(app.version or 1),
        updated_at=now,
    )
    _write_receipt(app, receipt)


def surface_auto_reject_manual_reconciliation(
    db,
    *,
    app: CandidateApplication,
    decision: dict[str, Any],
    provider: str,
    provider_result: dict[str, Any],
    drift_reason: str,
    actor_type: str,
) -> dict[str, Any]:
    """Surface remote success without overwriting newer local authority."""

    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
    )

    operation_id = str(decision.get("operation_id") or "")
    receipt = auto_reject_operation_receipt(app) or {}
    now = _now()
    if str(receipt.get("operation_id") or "") == operation_id:
        receipt.update(
            status="manual_reconciliation_required",
            reconciliation_reason=drift_reason,
            provider_succeeded_at=now,
            provider_called=True,
            provider_succeeded=True,
            provider_outcome_uncertain=False,
            manual_reconciliation_required=True,
            observed_application_outcome=str(
                app.application_outcome or "open"
            ).strip().lower(),
            observed_application_version=int(app.version or 1),
            provider_result_code=provider_result.get("code"),
            updated_at=now,
        )
        _write_receipt(app, receipt)
    provider_label = "Bullhorn" if provider == "bullhorn" else "Workable"
    local_outcome = str(app.application_outcome or "open").strip().lower()
    message = sanitize_text_for_storage(
        f"{provider_label} confirmed the automatic rejection, but Taali kept "
        f"the newer local outcome '{local_outcome}' because authority changed "
        f"({drift_reason}). Manual provider reconciliation is required."
    )
    mark_auto_reject_state(
        app,
        state="manual_reconciliation_required",
        reason=message,
        triggered=False,
    )
    append_application_event(
        db,
        app=app,
        event_type="auto_reject_manual_reconciliation_required",
        actor_type=actor_type,
        reason=message,
        metadata={
            "operation_id": operation_id,
            "ats_provider": provider,
            "provider_succeeded": True,
            "provider_result_code": provider_result.get("code"),
            "local_outcome_preserved": local_outcome,
            "drift_reason": drift_reason,
        },
        idempotency_key=(f"{operation_id}:reconcile"[:200] or None),
    )
    return {
        **decision,
        "performed": False,
        "provider_performed": True,
        "state": "manual_reconciliation_required",
        "provider": provider,
        "provider_result": provider_result,
        "reason": message,
    }


__all__ = [
    "AUTO_REJECT_OPERATION_KEY",
    "authorize_auto_reject_operation",
    "auto_reject_operation_drift_reason",
    "auto_reject_operation_receipt",
    "cancel_auto_reject_before_provider",
    "complete_auto_reject_operation",
    "fence_auto_reject_lifecycle_restore",
    "fence_auto_reject_outcome",
    "lock_application_outcome_for_transition",
    "mark_auto_reject_provider_call_started",
    "mark_auto_reject_terminal_failure",
    "supersede_auto_reject_operation",
    "surface_auto_reject_manual_reconciliation",
]
