"""Preservation-first, three-phase lifecycle for ATS stage moves."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_stage_move_claim import StageMoveClaim, claim_stage_move
from .ats_stage_move_finalization import (
    _append_reconciliation_event,
    StageMoveFinalization,
    finalize_stage_move_success,
)
from .ats_stage_move_provider import (
    StageMoveProviderFailure,
    StageMoveProviderPlan,
)
from .ats_stage_move_receipt import (
    append_stage_move_reconciliation_evidence,
    checkpoint_stage_move_provider_success,
    fail_stage_move_receipt,
    mark_stage_move_related_note,
    stage_move_receipt,
)
from .workable_actions_service import WorkableWritebackError


def _checkpoint_provider_success(
    db: Session, *, claim: StageMoveClaim, provider_result: dict[str, Any]
) -> bool:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == claim.snapshot.application_id,
            CandidateApplication.organization_id == claim.snapshot.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        db.rollback()
        return False
    current = stage_move_receipt(app)
    exact_active = bool(
        current is not None
        and str(current.get("operation_id") or "") == claim.operation_id
        and str(current.get("snapshot_fingerprint") or "")
        == claim.snapshot.operation_fingerprint()
        and str(current.get("status") or "") == "provider_call_started"
    )
    if not exact_active:
        append_stage_move_reconciliation_evidence(
            app,
            snapshot=claim.snapshot,
            operation_id=claim.operation_id,
            drift_reason="operation_receipt_replaced_after_provider_success",
            provider_remote_stage=provider_result.get("provider_remote_stage"),
            provider_called=True,
            provider_succeeded=True,
        )
        _append_reconciliation_event(
            db,
            app=app,
            claim=claim,
            reason="operation_receipt_replaced_after_provider_success",
            provider_succeeded=True,
        )
        db.commit()
        return False
    checkpoint_stage_move_provider_success(
        app,
        operation_id=claim.operation_id,
        snapshot_fingerprint=claim.snapshot.operation_fingerprint(),
        provider_result=provider_result,
    )
    db.commit()
    return True


def record_stage_move_provider_failure(
    db: Session, *, claim: StageMoveClaim, error: StageMoveProviderFailure
) -> None:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == claim.snapshot.application_id,
            CandidateApplication.organization_id == claim.snapshot.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        db.rollback()
        return
    current = stage_move_receipt(app)
    exact_active = bool(
        current is not None
        and str(current.get("operation_id") or "") == claim.operation_id
        and str(current.get("snapshot_fingerprint") or "")
        == claim.snapshot.operation_fingerprint()
        and str(current.get("status") or "") == "provider_call_started"
    )
    if exact_active:
        fail_stage_move_receipt(
            app,
            operation_id=claim.operation_id,
            error_code=error.code,
            error_message=error.message,
            provider_called=error.provider_called,
            retryable=error.retriable,
        )
    else:
        append_stage_move_reconciliation_evidence(
            app,
            snapshot=claim.snapshot,
            operation_id=claim.operation_id,
            drift_reason=f"provider_failure:{error.code}",
            provider_remote_stage=claim.snapshot.provider_remote_stage,
            provider_called=error.provider_called,
            provider_succeeded=False if error.provider_called is False else None,
        )
    if error.provider_called is None:
        _append_reconciliation_event(
            db,
            app=app,
            claim=claim,
            reason=f"provider_failure:{error.code}",
            provider_succeeded=None,
        )
    db.commit()


def _mark_note(
    db: Session, *, claim: StageMoveClaim, status: str, job_run_id: int | None = None
) -> None:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == claim.snapshot.application_id,
            CandidateApplication.organization_id == claim.snapshot.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is not None:
        mark_stage_move_related_note(
            app,
            operation_id=claim.operation_id,
            status=status,
            job_run_id=job_run_id,
        )
        db.commit()
    else:
        db.rollback()


def queue_stage_move_related_note(
    db: Session, *, claim: StageMoveClaim, note: dict[str, Any] | None
) -> None:
    if not isinstance(note, dict) or str(note.get("status") or "") == "queued":
        return
    from .ats_job_run_errors import AtsJobRunPersistenceError
    from .ats_note_dispatch import AtsNoteQueueError, enqueue_application_ats_note

    try:
        job_run_id = enqueue_application_ats_note(
            db,
            organization_id=claim.snapshot.organization_id,
            application_id=claim.snapshot.application_id,
            body=str(note.get("body") or ""),
            provider=claim.snapshot.provider,
            actor_type=str(note.get("actor_type") or "recruiter"),
            actor_id=(
                int(note["actor_id"])
                if note.get("actor_id") is not None
                else None
            ),
            dispatch_key=str(note["dispatch_key"]),
            expected_provider_target_id=claim.snapshot.provider_target_id,
            expected_candidate_provider_id=claim.snapshot.candidate_provider_id,
        )
    except AtsNoteQueueError as exc:
        _mark_note(db, claim=claim, status="queue_failed")
        raise WorkableWritebackError(
            action="note",
            code=exc.code,
            message=exc.message,
            retriable=False,
        ) from None
    except AtsJobRunPersistenceError:
        _mark_note(db, claim=claim, status="queue_failed")
        raise WorkableWritebackError(
            action="note",
            code="note_tracking_unavailable",
            message="The confirmed move's related-role audit note could not be queued",
            retriable=True,
        ) from None
    _mark_note(db, claim=claim, status="queued", job_run_id=int(job_run_id))


def execute_stage_move_lifecycle(
    db: Session,
    *,
    organization_id: int,
    payload: dict,
    provider_call: Callable[[StageMoveProviderPlan], dict[str, Any]] | None = None,
    should_yield: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run claim -> lock-free provider call -> exact finalization."""

    if provider_call is None:
        # Resolve at call time so tests and deployments can instrument the
        # canonical provider boundary without relying on import order.
        from .ats_stage_move_provider import perform_stage_move_provider_call

        provider_call = perform_stage_move_provider_call

    claim = claim_stage_move(db, organization_id=organization_id, payload=payload)
    if claim.disposition == "reconciliation_required":
        return {
            "status": "reconciliation_required",
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
            "failed": True,
        }
    if claim.disposition == "confirmed_replay":
        result = {
            "status": "ok",
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
            "replayed": True,
        }
        note = claim.receipt.get("related_note")
        queue_stage_move_related_note(
            db, claim=claim, note=note if isinstance(note, dict) else None
        )
        return result
    if claim.disposition == "finalize_provider_success":
        provider_was_called = False
        stored_result = claim.receipt.get("provider_result")
        stored_result = stored_result if isinstance(stored_result, dict) else {}
        provider_result = {
            "success": True,
            "code": stored_result.get("code") or "ok",
            "provider_remote_stage": (
                stored_result.get("provider_remote_stage")
                or claim.receipt.get("provider_remote_stage")
            ),
            "response_id": stored_result.get("response_id"),
        }
    else:
        provider_was_called = True
        if db.in_transaction():
            raise RuntimeError("ATS provider call cannot run inside a database transaction")
        assert claim.provider_plan is not None
        try:
            if should_yield is not None and should_yield():
                raise StageMoveProviderFailure(
                    code="mutex_lease_lost",
                    message="ATS mutex ownership became uncertain before the stage move",
                    provider_called=False,
                    retriable=True,
                )
            provider_result = provider_call(claim.provider_plan)
            if not isinstance(provider_result, dict) or not provider_result.get("success"):
                raise StageMoveProviderFailure(
                    code="api_error",
                    message="ATS returned an invalid stage-move receipt",
                    provider_called=None,
                    retriable=True,
                )
        except StageMoveProviderFailure as exc:
            record_stage_move_provider_failure(db, claim=claim, error=exc)
            wrapped = WorkableWritebackError(
                action="move",
                code=exc.code,
                message=exc.message,
                retriable=(exc.retriable and exc.provider_called is False),
            )
            wrapped.provider_called = exc.provider_called
            raise wrapped from None
        except Exception:
            error = StageMoveProviderFailure(
                code="api_error",
                message="ATS stage move did not confirm; verify the remote stage",
                provider_called=None,
                retriable=True,
            )
            record_stage_move_provider_failure(db, claim=claim, error=error)
            wrapped = WorkableWritebackError(
                action="move",
                code=error.code,
                message=error.message,
                retriable=False,
            )
            wrapped.provider_called = None
            raise wrapped from None
    if provider_was_called and not _checkpoint_provider_success(
        db, claim=claim, provider_result=provider_result
    ):
        return {
            "status": "reconciliation_required",
            "application_id": claim.snapshot.application_id,
            "operation_id": claim.operation_id,
            "provider": claim.snapshot.provider,
            "reconciliation_reason": "operation_receipt_replaced_after_provider_success",
            "failed": True,
        }
    finalization = finalize_stage_move_success(
        db, claim=claim, provider_result=provider_result
    )
    queue_stage_move_related_note(db, claim=claim, note=finalization.related_note)
    return finalization.result


__all__ = [
    "StageMoveFinalization",
    "execute_stage_move_lifecycle",
    "finalize_stage_move_success",
    "queue_stage_move_related_note",
    "record_stage_move_provider_failure",
]
