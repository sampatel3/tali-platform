"""Two-phase synchronous Workable outcome delivery for recruiter PATCHes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_writeback_state import (
    OUTCOME_WRITEBACK_KEY,
    OUTCOME_WRITEBACK_RECONCILIATION_KEY,
    set_outcome_writeback_state,
)
from .document_service import sanitize_json_for_storage
from .manual_outcome_identity import build_manual_outcome_operation_id

_PROVIDER_NOT_CALLED_CODES = frozenset(
    {
        "missing_actor_member_id",
        "missing_candidate_id",
        "missing_connection",
        "missing_write_scope",
        "lifecycle_changed",
        "not_configured",
        "not_linked",
        "not_writeable",
        "writeback_disabled",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _receipt_matches(app: CandidateApplication, claim: dict) -> bool:
    state = app.integration_sync_state
    receipt = state.get(OUTCOME_WRITEBACK_KEY) if isinstance(state, dict) else None
    if not isinstance(receipt, dict):
        return False
    try:
        return bool(
            int(receipt.get("application_id")) == int(app.id)
            and str(receipt.get("operation_id") or "") == claim["operation_id"]
            and str(receipt.get("provider") or "") == "workable"
            and str(receipt.get("provider_target_id") or "")
            == claim["provider_target_id"]
            and int(receipt.get("expected_application_version"))
            == claim["expected_application_version"]
            and str(receipt.get("expected_local_outcome") or "")
            == claim["expected_local_outcome"]
            and str(receipt.get("target_outcome") or "")
            == claim["target_outcome"]
        )
    except (TypeError, ValueError):
        return False


def _lifecycle_matches(app: CandidateApplication, claim: dict) -> bool:
    return bool(
        app.deleted_at is None
        and int(app.version or 0) == claim["expected_application_version"]
        and str(app.application_outcome or "open").strip().lower()
        == claim["expected_local_outcome"]
        and str(app.workable_candidate_id or "").strip()
        == claim["provider_target_id"]
    )


def begin_synchronous_workable_outcome(
    db: Session,
    app: CandidateApplication,
    *,
    organization_id: int,
    target_outcome: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Persist an exact provider claim, then release all DB locks before HTTP."""

    from .auto_reject_operation_receipt import fence_auto_reject_outcome

    current_outcome = str(app.application_outcome or "open").strip().lower()
    target = str(target_outcome or "").strip().lower()
    provider_target_id = str(app.workable_candidate_id or "").strip()
    if not provider_target_id:
        raise HTTPException(
            status_code=409,
            detail="Application is no longer linked to its Workable candidate.",
        )
    fence_auto_reject_outcome(
        db,
        app,
        target,
        "recruiter",
        None,
        already_locked=True,
    )
    operation_id = build_manual_outcome_operation_id(
        organization_id=organization_id,
        application_id=int(app.id),
        application_version=int(app.version),
        target_outcome=f"workable-sync:{target}",
        idempotency_key=idempotency_key,
    )
    claim = {
        "application_id": int(app.id),
        "organization_id": int(organization_id),
        "operation_id": operation_id,
        "provider": "workable",
        "provider_target_id": provider_target_id,
        "expected_application_version": int(app.version),
        "expected_local_outcome": current_outcome,
        "target_outcome": target,
    }
    set_outcome_writeback_state(
        app,
        provider="workable",
        status="provider_call_started",
        target_outcome=target,
        expected_application_version=int(app.version),
        expected_local_outcome=current_outcome,
        operation_id=operation_id,
        provider_target_id=provider_target_id,
    )
    db.commit()
    return claim


def _lock_current_application(
    db: Session, organization_id: int, application_id: int
) -> CandidateApplication | None:
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )


def _record_post_provider_state(
    db: Session,
    app: CandidateApplication,
    claim: dict,
    *,
    provider_called: bool | None,
    provider_succeeded: bool | None,
    reason: str,
    error_code: str | None = None,
) -> None:
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    state = (
        dict(app.integration_sync_state)
        if isinstance(app.integration_sync_state, dict)
        else {}
    )
    current = state.get(OUTCOME_WRITEBACK_KEY)
    current = dict(current) if isinstance(current, dict) else {}
    needs_reconciliation = provider_succeeded is not False
    status = "manual_reconciliation_required" if needs_reconciliation else "failed"
    receipt = {
        **(current if _receipt_matches(app, claim) else {}),
        **claim,
        "status": status,
        "provider_called": provider_called,
        "provider_succeeded": provider_succeeded,
        "provider_outcome_uncertain": provider_succeeded is None,
        "manual_reconciliation_required": needs_reconciliation,
        "observed_application_version": int(app.version or 0),
        "observed_application_outcome": str(
            app.application_outcome or "open"
        ).strip().lower(),
        "updated_at": _now(),
    }
    if error_code:
        receipt["error_code"] = str(error_code)[:100]
    key = (
        OUTCOME_WRITEBACK_KEY
        if _receipt_matches(app, claim)
        else OUTCOME_WRITEBACK_RECONCILIATION_KEY
    )
    state[key] = receipt
    app.integration_sync_state = sanitize_json_for_storage(state)
    append_application_event(
        db,
        app=app,
        event_type=(
            "ats_outcome_writeback_manual_reconciliation_required"
            if needs_reconciliation
            else "workable_writeback_failed"
        ),
        actor_type="system",
        reason=reason,
        metadata={
            "operation_id": claim["operation_id"],
            "ats": "workable",
            "code": error_code,
            "provider_succeeded": provider_succeeded,
        },
        idempotency_key=f"{claim['operation_id']}:post-provider"[:200],
    )
    db.commit()


def surface_synchronous_workable_failure(
    db: Session,
    *,
    organization_id: int,
    claim: dict,
    error_code: str,
    error_message: str,
) -> None:
    app = _lock_current_application(db, organization_id, claim["application_id"])
    if app is None:
        db.rollback()
        return
    definitely_not_called = str(error_code or "") in _PROVIDER_NOT_CALLED_CODES
    _record_post_provider_state(
        db,
        app,
        claim,
        provider_called=False if definitely_not_called else None,
        provider_succeeded=False if definitely_not_called else None,
        error_code=error_code,
        reason=(
            f"Workable did not accept the outcome update. {error_message}"
            if definitely_not_called
            else "Workable outcome is uncertain; verify it before retrying."
        ),
    )


def surface_synchronous_workable_success_drift(
    db: Session,
    *,
    organization_id: int,
    claim: dict,
    reason: str,
) -> None:
    app = _lock_current_application(db, organization_id, claim["application_id"])
    if app is None:
        db.rollback()
        return
    _record_post_provider_state(
        db,
        app,
        claim,
        provider_called=True,
        provider_succeeded=True,
        reason=reason,
    )


def complete_synchronous_workable_outcome(
    db: Session,
    app: CandidateApplication,
    claim: dict,
    *,
    actor_id: int,
    reason: str | None,
    idempotency_key: str | None,
    acting_role_id: int | None,
    provider_result: dict,
) -> CandidateApplication:
    """Apply local state only if post-I/O lifecycle and exact claim still match."""

    if not _receipt_matches(app, claim) or not _lifecycle_matches(app, claim):
        surface_synchronous_workable_success_drift(
            db,
            organization_id=int(app.organization_id),
            claim=claim,
            reason=(
                "Workable confirmed the outcome, but local lifecycle authority "
                "changed during delivery. The local state was preserved."
            ),
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Workable changed, but the application changed concurrently. "
                "The local outcome was preserved and now needs reconciliation."
            ),
        )

    from ..domains.assessments_runtime.pipeline_service import (
        append_application_event,
        transition_outcome,
    )

    transition_outcome(
        db,
        app=app,
        to_outcome=claim["target_outcome"],
        actor_type="recruiter",
        actor_id=actor_id,
        reason=reason or "Recruiter outcome update",
        metadata=(
            {"acting_role_id": int(acting_role_id)}
            if acting_role_id is not None
            else None
        ),
        idempotency_key=idempotency_key,
        expected_version=claim["expected_application_version"],
        operation_receipt_key=claim["operation_id"],
    )
    set_outcome_writeback_state(
        app,
        provider="workable",
        status="confirmed",
        target_outcome=claim["target_outcome"],
        expected_application_version=claim["expected_application_version"],
        expected_local_outcome=claim["expected_local_outcome"],
        operation_id=claim["operation_id"],
        provider_target_id=claim["provider_target_id"],
    )
    append_application_event(
        db,
        app=app,
        event_type=(
            "workable_reverted"
            if claim["target_outcome"] == "open"
            else "workable_disqualified"
        ),
        actor_type="recruiter",
        actor_id=actor_id,
        reason=reason or provider_result.get("message") or "Workable outcome synced",
        metadata={
            "action": provider_result.get("action"),
            "code": provider_result.get("code"),
            "workable_candidate_id": claim["provider_target_id"],
            "workable_actor_member_id": (provider_result.get("config") or {}).get(
                "actor_member_id"
            ),
            "workable_disqualify_reason_id": (
                provider_result.get("config") or {}
            ).get("workable_disqualify_reason_id"),
        },
    )
    db.commit()
    db.refresh(app)
    return app


__all__ = [
    "begin_synchronous_workable_outcome",
    "complete_synchronous_workable_outcome",
    "surface_synchronous_workable_failure",
    "surface_synchronous_workable_success_drift",
]
