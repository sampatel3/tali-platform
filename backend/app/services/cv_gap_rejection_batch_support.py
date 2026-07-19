"""Progress and reconciliation helpers for the durable CV-gap batch."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.background_job_run import BackgroundJobRun
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import Role
from ..schemas.role import RoleFamilyResponse
from . import background_job_runs
from .cv_gap_rejection import finalize_cv_gap_provider_reject
from .cv_gap_rejection_authority import CvGapAuthorityConflict
from .cv_gap_rejection_context import (
    load_cv_gap_execution_context,
    lock_cv_gap_application,
)
from .cv_gap_rejection_receipt import (
    complete_cv_gap_rejection,
    cv_gap_receipt_drift_reason,
    cv_gap_rejection_receipt,
    fail_cv_gap_rejection,
    mark_cv_gap_provider_succeeded,
    surface_cv_gap_manual_reconciliation,
)
from .document_service import sanitize_text_for_storage
from .workable_actions_service import WorkableWritebackError


def initial_cv_gap_rejection_progress(application_ids: list[int]) -> dict[str, Any]:
    ids = [int(value) for value in application_ids]
    return {
        "application_ids": ids,
        "total_count": len(ids),
        "processed_application_ids": [],
        "processed_count": 0,
        "remaining_count": len(ids),
        "rejected_application_ids": [],
        "rejected_count": 0,
        "skipped": [],
        "skipped_count": 0,
        "failures": [],
        "failure_count": 0,
        "authority_failure": None,
        "in_flight": None,
    }


def load_progress(
    db: Session,
    *,
    job_run_id: int | None,
    organization_id: int,
    application_ids: list[int],
) -> dict[str, Any]:
    initial = initial_cv_gap_rejection_progress(application_ids)
    if not job_run_id:
        return initial
    row = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.id == int(job_run_id),
            BackgroundJobRun.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    counters = row.counters if row is not None and isinstance(row.counters, dict) else {}
    stored = counters.get("progress") if isinstance(counters, dict) else None
    if not isinstance(stored, dict):
        return initial
    if [int(value) for value in stored.get("application_ids") or []] != application_ids:
        return initial
    return {**initial, **stored, "application_ids": list(application_ids)}


def update_progress_counts(progress: dict[str, Any]) -> None:
    processed = [int(value) for value in progress.get("processed_application_ids") or []]
    progress["processed_application_ids"] = processed
    progress["processed_count"] = len(processed)
    progress["remaining_count"] = max(
        0, int(progress.get("total_count") or 0) - len(processed)
    )
    progress["rejected_count"] = len(progress.get("rejected_application_ids") or [])
    progress["skipped_count"] = len(progress.get("skipped") or [])
    progress["failure_count"] = len(progress.get("failures") or [])


def persist_progress(job_run_id: int | None, progress: dict[str, Any]) -> bool:
    if not job_run_id:
        return True
    return background_job_runs.merge_progress(int(job_run_id), progress)


def record_processed(
    progress: dict[str, Any],
    *,
    application_id: int,
    outcome: str,
    reason: str | None = None,
) -> None:
    app_id = int(application_id)
    progress["in_flight"] = None
    processed = progress.setdefault("processed_application_ids", [])
    if app_id not in processed:
        processed.append(app_id)
    if outcome == "rejected":
        rejected = progress.setdefault("rejected_application_ids", [])
        if app_id not in rejected:
            rejected.append(app_id)
    elif outcome == "skipped":
        progress.setdefault("skipped", []).append(
            {"application_id": app_id, "reason": reason or "no longer eligible"}
        )
    else:
        progress.setdefault("failures", []).append(
            {"application_id": app_id, "reason": reason or "ATS reject failed"}
        )
    update_progress_counts(progress)


def record_authority_failure(progress: dict[str, Any], exc: Exception) -> None:
    if isinstance(exc, CvGapAuthorityConflict):
        progress["authority_failure"] = {
            "code": exc.code,
            "message": exc.message,
            **(
                {"current_preview": exc.current_preview}
                if exc.current_preview is not None
                else {}
            ),
        }
    else:
        progress["authority_failure"] = {
            "code": "JOB_PERMISSION_CHANGED",
            "message": "The approving recruiter no longer controls this job.",
            "status_code": int(getattr(exc, "status_code", 403)),
        }
    update_progress_counts(progress)


def _safe_provider_result(result: dict[str, Any]) -> dict[str, Any]:
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    return {
        "provider": str(result.get("provider") or "local"),
        "provider_target_id": str(result.get("provider_target_id") or ""),
        "write_required": bool(result.get("write_required")),
        "success": bool(result.get("success")),
        "code": sanitize_text_for_storage(str(result.get("code") or "")),
        "message": sanitize_text_for_storage(str(result.get("message") or "")),
        "config": {
            "remote_status": sanitize_text_for_storage(
                str(config.get("remote_status") or "")
            )
        },
    }


def set_in_flight(
    progress: dict[str, Any],
    *,
    application_id: int,
    operation_id: str,
    status: str,
    provider_result: dict[str, Any] | None = None,
) -> None:
    receipt = {
        "application_id": int(application_id),
        "operation_id": str(operation_id),
        "status": str(status),
    }
    if provider_result is not None:
        receipt["provider_result"] = _safe_provider_result(provider_result)
    progress["in_flight"] = receipt
    update_progress_counts(progress)


def matching_in_flight(
    progress: dict[str, Any],
    *,
    application_id: int,
    operation_id: str,
) -> dict[str, Any] | None:
    value = progress.get("in_flight")
    if not isinstance(value, dict):
        return None
    if int(value.get("application_id") or 0) != int(application_id):
        return None
    if str(value.get("operation_id") or "") != str(operation_id):
        return None
    return value


def exact_rejection_applied(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    operation_id: str,
) -> bool:
    """Whether this exact operation durably applied, despite later overrides."""

    return (
        db.query(CandidateApplicationEvent.id)
        .filter(
            CandidateApplicationEvent.application_id == int(application_id),
            CandidateApplicationEvent.organization_id == int(organization_id),
            CandidateApplicationEvent.idempotency_key
            == f"{operation_id}:outcome"[:200],
        )
        .first()
        is not None
    )


def surface_manual_reconciliation(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    operation_id: str,
    provider: str,
    reason: str,
    user_id: int,
    provider_succeeded: bool | None,
) -> None:
    app = lock_cv_gap_application(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
    )
    if app is None:
        db.rollback()
        return
    surface_cv_gap_manual_reconciliation(
        db,
        app=app,
        operation_id=operation_id,
        provider=provider,
        reason=reason,
        actor_id=user_id,
        provider_succeeded=provider_succeeded,
    )
    db.commit()


def finalize_provider_failure(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    operation_id: str,
    user_id: int,
    spec: dict[str, str],
    provider_result: dict[str, Any],
) -> str:
    app = lock_cv_gap_application(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
    )
    if app is None:
        db.rollback()
        return "application disappeared before provider failure could be recorded"
    outcome = finalize_cv_gap_provider_reject(
        db,
        app=app,
        actor_type="recruiter",
        actor_id=user_id,
        reason=spec["reason"],
        trigger=spec["trigger"],
        operation_id=operation_id,
        provider_result=provider_result,
    )
    reason = str(outcome.get("reason") or "ATS reject failed")
    fail_cv_gap_rejection(
        app,
        operation_id=operation_id,
        reason=reason,
        provider_called=bool(provider_result.get("write_required")),
    )
    db.commit()
    return reason


def persist_provider_success_receipt(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    operation_id: str,
    user_id: int,
    provider_result: dict[str, Any],
) -> None:
    """Commit exact provider success before any fallible downstream work."""
    app = lock_cv_gap_application(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
    )
    receipt = cv_gap_rejection_receipt(app) if app is not None else None
    if (
        app is None
        or receipt is None
        or str(receipt.get("operation_id") or "") != str(operation_id)
        or str(receipt.get("status") or "")
        not in {"provider_call_started", "provider_succeeded"}
    ):
        try:
            if app is not None:
                surface_cv_gap_manual_reconciliation(
                    db,
                    app=app,
                    operation_id=operation_id,
                    provider=str(provider_result.get("provider") or "local"),
                    reason=(
                        "The ATS confirmed rejection, but its exact operation receipt "
                        "changed. Manual reconciliation is required."
                    ),
                    actor_id=user_id,
                    provider_succeeded=True,
                )
                db.commit()
            else:
                db.rollback()
        except Exception as exc:
            db.rollback()
            raise WorkableWritebackError(
                action="reject_cv_gap",
                code="provider_success_receipt_changed",
                message="ATS rejection succeeded but its local receipt changed",
                retriable=False,
            ) from exc
        raise WorkableWritebackError(
            action="reject_cv_gap",
            code="provider_success_receipt_changed",
            message="ATS rejection succeeded but its local receipt changed",
            retriable=False,
        )
    try:
        mark_cv_gap_provider_succeeded(
            app,
            operation_id=operation_id,
            provider_result=provider_result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise WorkableWritebackError(
            action="reject_cv_gap",
            code="provider_success_receipt_failed",
            message="ATS rejection succeeded; durable reconciliation will be retried",
            retriable=True,
        ) from exc


def finalize_provider_success(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    needs_input_id: int,
    kind: str,
    user_id: int,
    expected_version: int,
    expected_family: RoleFamilyResponse,
    application_id: int,
    operation_id: str,
    spec: dict[str, str],
    provider_result: dict[str, Any],
) -> tuple[bool, str | None, CvGapAuthorityConflict | None]:
    authority_error: CvGapAuthorityConflict | None = None
    context = None
    ineligible = None
    try:
        context, ineligible = load_cv_gap_execution_context(
            db,
            organization_id=int(organization_id),
            role_id=role_id,
            needs_input_id=needs_input_id,
            kind=kind,
            user_id=user_id,
            expected_owner_role_version=expected_version,
            expected_role_family=expected_family,
            application_id=application_id,
            lock=True,
        )
    except CvGapAuthorityConflict as exc:
        authority_error = exc
    app = context.app if context is not None else lock_cv_gap_application(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
    )
    drift = authority_error.message if authority_error is not None else ineligible
    if drift is None and app is not None and context is not None:
        drift = cv_gap_receipt_drift_reason(
            app,
            operation_id=operation_id,
            owner_role_id=int(context.owner.id),
            kind=kind,
            provider_snapshot=context.provider_snapshot,
        )
    if app is None:
        db.rollback()
        return False, "application unavailable after ATS success", authority_error
    if drift is not None:
        message = (
            "The ATS confirmed rejection, but local authority changed "
            f"({drift}). Manual reconciliation is required."
        )
        surface_cv_gap_manual_reconciliation(
            db,
            app=app,
            operation_id=operation_id,
            provider=str(provider_result.get("provider") or "local"),
            reason=message,
            actor_id=user_id,
            provider_succeeded=True,
        )
        db.commit()
        return False, message, authority_error
    try:
        mark_cv_gap_provider_succeeded(
            app,
            operation_id=operation_id,
            provider_result=provider_result,
        )
        finalize_cv_gap_provider_reject(
            db,
            app=app,
            actor_type="recruiter",
            actor_id=user_id,
            reason=spec["reason"],
            trigger=spec["trigger"],
            operation_id=operation_id,
            provider_result=provider_result,
        )
        complete_cv_gap_rejection(app, operation_id=operation_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise WorkableWritebackError(
            action="reject_cv_gap",
            code="local_reconciliation_failed",
            message="ATS rejection succeeded; local reconciliation will be retried",
            retriable=True,
        ) from exc
    return True, None, None


def sync_readiness_once(
    db: Session,
    *,
    organization_id: int,
    owner_role_id: int,
) -> None:
    from ..agent_runtime import data_readiness
    owner = (
        db.query(Role)
        .filter(
            Role.id == int(owner_role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if owner is None:
        db.rollback()
        return
    try:
        data_readiness.sync_cv_readiness(db, role=owner)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise WorkableWritebackError(
            action="reject_cv_gap",
            code="readiness_sync_failed",
            message="Rejections were saved; CV-gap card refresh will be retried",
            retriable=True,
        ) from exc


__all__ = [
    "exact_rejection_applied",
    "finalize_provider_failure",
    "finalize_provider_success",
    "initial_cv_gap_rejection_progress",
    "load_progress",
    "matching_in_flight",
    "persist_progress",
    "persist_provider_success_receipt",
    "record_authority_failure",
    "record_processed",
    "set_in_flight",
    "surface_manual_reconciliation",
    "sync_readiness_once",
    "update_progress_counts",
]
