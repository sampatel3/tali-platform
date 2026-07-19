"""Durable per-application worker for a confirmed CV-gap cohort."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..schemas.role import RoleFamilyResponse
from .cv_gap_rejection import CvGapProviderChanged, perform_cv_gap_provider_reject
from .cv_gap_rejection_authority import (
    CV_GAP_REJECTION_SPECS,
    CvGapAuthorityConflict,
    MAX_CV_GAP_REJECTION_BATCH,
)
from .cv_gap_rejection_context import (
    cv_gap_operation_id,
    load_cv_gap_execution_context,
    lock_cv_gap_application,
)
from .cv_gap_rejection_receipt import (
    authorize_cv_gap_rejection,
    cv_gap_receipt_drift_reason,
    cv_gap_rejection_receipt,
    defer_cv_gap_provider_call,
    fail_cv_gap_rejection,
    mark_cv_gap_provider_call_started,
    provider_result_from_cv_gap_receipt,
)
from .cv_gap_rejection_batch_support import (
    exact_rejection_applied as _exact_rejection_applied,
    finalize_provider_success as _finalize_provider_success,
    initial_cv_gap_rejection_progress,
    load_progress as _load_progress,
    matching_in_flight as _matching_in_flight,
    persist_progress as _persist_progress,
    persist_provider_success_receipt as _persist_provider_success_receipt,
    record_authority_failure as _authority_failure,
    record_processed as _record_processed,
    set_in_flight as _set_in_flight,
    surface_manual_reconciliation as _surface_manual_reconciliation,
    sync_readiness_once as _sync_readiness_once,
    update_progress_counts as _update_progress_counts,
)
from .cv_gap_rejection_batch_result import CvGapSuccessReconciler
from .workable_actions_service import WorkableWritebackError


def run_cv_gap_rejection_batch(
    db: Session,
    organization_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Drain one confirmed cohort with lock-free provider I/O per item."""
    should_yield = payload.get("_should_yield")
    kind = str(payload.get("kind") or "")
    raw_ids = payload.get("application_ids")
    if kind not in CV_GAP_REJECTION_SPECS or not isinstance(raw_ids, list):
        raise ValueError("invalid CV-gap rejection payload")
    application_ids = [int(value) for value in raw_ids]
    if (
        not application_ids
        or len(application_ids) > MAX_CV_GAP_REJECTION_BATCH
        or application_ids != sorted(set(application_ids))
    ):
        raise ValueError("application_ids must be unique, ascending, and contain 1-200 IDs")

    role_id = int(payload["role_id"])
    needs_input_id = int(payload["needs_input_id"])
    user_id = int(payload["user_id"])
    expected_version = int(payload["expected_owner_role_version"])
    expected_family = RoleFamilyResponse.model_validate(payload["expected_role_family"])
    job_run_id = int(payload["_job_run_id"]) if payload.get("_job_run_id") else None
    progress = _load_progress(
        db,
        job_run_id=job_run_id,
        organization_id=int(organization_id),
        application_ids=application_ids,
    )
    processed = {int(value) for value in progress.get("processed_application_ids") or []}
    spec = CV_GAP_REJECTION_SPECS[kind]
    reconcile_success = CvGapSuccessReconciler(
        db=db,
        organization_id=int(organization_id),
        role_id=role_id,
        needs_input_id=needs_input_id,
        kind=kind,
        user_id=user_id,
        expected_version=expected_version,
        expected_family=expected_family,
        job_run_id=job_run_id,
        progress=progress,
        spec=spec,
        finalize=_finalize_provider_success,
    )
    mutex_lease_lost = False

    for application_id in application_ids:
        if application_id in processed:
            continue
        if mutex_lease_lost := bool(should_yield and should_yield()):
            break
        operation_id = cv_gap_operation_id(
            job_run_id=job_run_id, needs_input_id=needs_input_id,
            kind=kind, application_id=application_id,
        )
        if _exact_rejection_applied(
            db,
            organization_id=int(organization_id),
            application_id=application_id,
            operation_id=operation_id,
        ):
            db.rollback()
            _record_processed(
                progress,
                application_id=application_id,
                outcome="rejected",
                reason=spec["reason"],
            )
            _persist_progress(job_run_id, progress)
            processed.add(application_id)
            continue
        in_flight = _matching_in_flight(
            progress, application_id=application_id, operation_id=operation_id,
        )
        app_snapshot = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == int(organization_id),
            )
            .one_or_none()
        )
        receipt = cv_gap_rejection_receipt(app_snapshot) if app_snapshot else None
        receipt_status = (
            str(receipt.get("status") or "")
            if receipt and str(receipt.get("operation_id") or "") == operation_id
            else ""
        )
        db.rollback()
        if receipt_status == "provider_succeeded":
            result = provider_result_from_cv_gap_receipt(receipt)
            assert result is not None
            stop = reconcile_success(
                application_id=application_id,
                operation_id=operation_id,
                provider_result=result,
            )
            processed.add(application_id)
            if stop:
                break
            continue
        if in_flight and in_flight.get("status") == "provider_succeeded":
            result = dict(in_flight.get("provider_result") or {})
            stop = reconcile_success(
                application_id=application_id,
                operation_id=operation_id,
                provider_result=result,
            )
            processed.add(application_id)
            if stop:
                break
            continue
        if in_flight and in_flight.get("status") == "provider_failed":
            result = dict(in_flight.get("provider_result") or {})
            reconcile_success.provider_failure(
                application_id=application_id,
                operation_id=operation_id,
                provider_result=result,
            )
            processed.add(application_id)
            continue
        if receipt_status in {
            "completed",
            "failed",
            "manual_reconciliation_required",
        }:
            _record_processed(
                progress,
                application_id=application_id,
                outcome="failed",
                reason=str(
                    (receipt or {}).get("failure_reason")
                    or (receipt or {}).get("reconciliation_reason")
                    or (
                        "Completed receipt lacks exact outcome evidence"
                        if receipt_status == "completed"
                        else "ATS rejection requires review"
                    )
                ),
            )
            _persist_progress(job_run_id, progress)
            processed.add(application_id)
            continue
        if receipt_status == "provider_call_started" and bool(
            (receipt or {}).get("provider_write_required")
        ):
            reason = (
                "The prior worker stopped during ATS rejection; provider outcome "
                "is uncertain and requires manual reconciliation."
            )
            _surface_manual_reconciliation(
                db,
                organization_id=int(organization_id),
                application_id=application_id,
                operation_id=operation_id,
                provider=str((receipt or {}).get("provider") or "local"),
                reason=reason,
                user_id=user_id,
                provider_succeeded=None,
            )
            _record_processed(
                progress,
                application_id=application_id,
                outcome="failed",
                reason=reason,
            )
            _persist_progress(job_run_id, progress)
            processed.add(application_id)
            continue
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
            if ineligible is not None:
                db.rollback()
                _record_processed(
                    progress,
                    application_id=application_id,
                    outcome="skipped",
                    reason=ineligible,
                )
                _persist_progress(job_run_id, progress)
                processed.add(application_id)
                continue
            assert context is not None
            if receipt_status != "authorized":
                authorize_cv_gap_rejection(
                    context.app,
                    operation_id=operation_id,
                    needs_input_id=needs_input_id,
                    kind=kind,
                    owner_role_id=int(context.owner.id),
                    expected_owner_role_version=expected_version,
                    provider_snapshot=context.provider_snapshot,
                )
                db.commit()
                _set_in_flight(
                    progress,
                    application_id=application_id,
                    operation_id=operation_id,
                    status="authorized",
                )
                _persist_progress(job_run_id, progress)
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
                if ineligible is not None or context is None:
                    db.rollback()
                    _record_processed(
                        progress,
                        application_id=application_id,
                        outcome="skipped",
                        reason=ineligible,
                    )
                    _persist_progress(job_run_id, progress)
                    processed.add(application_id)
                    continue
            drift = cv_gap_receipt_drift_reason(
                context.app,
                operation_id=operation_id,
                owner_role_id=int(context.owner.id),
                kind=kind,
                provider_snapshot=context.provider_snapshot,
            )
            if drift is not None:
                fail_cv_gap_rejection(
                    context.app,
                    operation_id=operation_id,
                    reason=drift,
                    provider_called=False,
                )
                db.commit()
                _record_processed(
                    progress,
                    application_id=application_id,
                    outcome="skipped",
                    reason=drift,
                )
                _persist_progress(job_run_id, progress)
                processed.add(application_id)
                continue
            if mutex_lease_lost := bool(should_yield and should_yield()):
                db.rollback()
                break
            mark_cv_gap_provider_call_started(context.app, operation_id=operation_id)
            db.commit()
            _set_in_flight(
                progress,
                application_id=application_id,
                operation_id=operation_id,
                status="provider_call_started",
            )
            _persist_progress(job_run_id, progress)
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
                lock=False,
            )
            if ineligible is not None or context is None:
                db.rollback()
                app = lock_cv_gap_application(
                    db,
                    organization_id=int(organization_id),
                    application_id=application_id,
                )
                if app is not None:
                    fail_cv_gap_rejection(
                        app,
                        operation_id=operation_id,
                        reason=ineligible or "application changed before ATS call",
                        provider_called=False,
                    )
                    db.commit()
                _record_processed(
                    progress,
                    application_id=application_id,
                    outcome="skipped",
                    reason=ineligible,
                )
                _persist_progress(job_run_id, progress)
                processed.add(application_id)
                continue
            try:
                if mutex_lease_lost := bool(should_yield and should_yield()):
                    defer_cv_gap_provider_call(context.app, operation_id=operation_id)
                    db.commit()
                    break
                provider_result = perform_cv_gap_provider_reject(
                    db,
                    org=context.org,
                    app=context.app,
                    role=context.owner,
                    reason=spec["reason"],
                    expected_provider_snapshot=context.provider_snapshot,
                )
            except CvGapProviderChanged:
                db.rollback()
                reason = (
                    "ATS routing changed before rejection; no provider call was made."
                )
                app = lock_cv_gap_application(
                    db,
                    organization_id=int(organization_id),
                    application_id=application_id,
                )
                if app is not None:
                    fail_cv_gap_rejection(
                        app,
                        operation_id=operation_id,
                        reason=reason,
                        provider_called=False,
                    )
                    db.commit()
                _record_processed(
                    progress,
                    application_id=application_id,
                    outcome="failed",
                    reason=reason,
                )
                _persist_progress(job_run_id, progress)
                processed.add(application_id)
                continue
            except Exception as exc:
                db.rollback()
                reason = (
                    "ATS outcome is uncertain after provider error "
                    f"({type(exc).__name__}); manual reconciliation is required."
                )
                _surface_manual_reconciliation(
                    db,
                    organization_id=int(organization_id),
                    application_id=application_id,
                    operation_id=operation_id,
                    provider=str(context.provider_snapshot.get("provider") or "local"),
                    reason=reason,
                    user_id=user_id,
                    provider_succeeded=None,
                )
                _record_processed(
                    progress,
                    application_id=application_id,
                    outcome="failed",
                    reason=reason,
                )
                _persist_progress(job_run_id, progress)
                processed.add(application_id)
                continue
            db.rollback()
            if provider_result.get("success"):
                _persist_provider_success_receipt(
                    db,
                    organization_id=int(organization_id),
                    application_id=application_id,
                    operation_id=operation_id,
                    user_id=user_id,
                    provider_result=provider_result,
                )
            _set_in_flight(
                progress,
                application_id=application_id,
                operation_id=operation_id,
                status=(
                    "provider_succeeded"
                    if provider_result.get("success")
                    else "provider_failed"
                ),
                provider_result=provider_result,
            )
            _persist_progress(job_run_id, progress)
            if provider_result.get("success"):
                stop = reconcile_success(
                    application_id=application_id,
                    operation_id=operation_id,
                    provider_result=provider_result,
                )
                processed.add(application_id)
                if stop:
                    break
            else:
                reconcile_success.provider_failure(
                    application_id=application_id,
                    operation_id=operation_id,
                    provider_result=provider_result,
                )
                processed.add(application_id)
        except CvGapAuthorityConflict as exc:
            db.rollback()
            _authority_failure(progress, exc)
            _persist_progress(job_run_id, progress)
            break
        except HTTPException as exc:
            db.rollback()
            _authority_failure(progress, exc)
            _persist_progress(job_run_id, progress)
            break
        except WorkableWritebackError:
            raise
        except Exception:
            db.rollback()
            _record_processed(
                progress,
                application_id=application_id,
                outcome="failed",
                reason="unexpected error during ATS reject",
            )
            _persist_progress(job_run_id, progress)
            processed.add(application_id)

    _sync_readiness_once(
        db,
        organization_id=int(organization_id),
        owner_role_id=int(expected_family.owner.id),
    )
    _update_progress_counts(progress)
    return {
        "progress": progress,
        "failed": bool(progress.get("failure_count") or progress.get("authority_failure")),
        **({"mutex_lease_lost": True} if mutex_lease_lost else {}),
    }


__all__ = ["initial_cv_gap_rejection_progress", "run_cv_gap_rejection_batch"]
