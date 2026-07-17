"""Exact, non-replayable ATS note delivery with a durable ambiguity fence."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_note_claim import (
    ensure_note_operation_payload,
    note_body_fingerprint,
    prepare_ats_note_delivery,
)
from .ats_note_provider import (
    AtsNoteProviderFailure,
    AtsNoteProviderPlan,
    perform_ats_note_provider_call,
)
from .ats_note_receipt import (
    ATS_NOTE_WRITEBACK_HISTORY_KEY,
    ATS_NOTE_WRITEBACK_KEY,
    archive_orphaned_note_result,
    note_receipt,
    note_receipt_matches,
    note_receipt_now,
    write_note_receipt,
)


def checkpoint_ats_note_provider_success(
    db: Session,
    *,
    plan: AtsNoteProviderPlan,
    provider_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist confirmed provider evidence before any local audit side effect."""

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == plan.application_id,
            CandidateApplication.organization_id == plan.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    current = note_receipt(app) if app is not None else None
    if app is not None and current is not None and note_receipt_matches(current, plan):
        status = str(current.get("status") or "")
        if status in {"provider_succeeded", "confirmed"}:
            db.rollback()
            return None
        if status == "provider_call_started":
            now = note_receipt_now()
            evidence = {
                key: provider_result[key]
                for key in ("provider", "provider_confirmed", "provider_receipt_id")
                if key in provider_result
            }
            current.update(
                status="provider_succeeded",
                provider_called=True,
                provider_succeeded=True,
                provider_outcome_uncertain=False,
                manual_reconciliation_required=False,
                provider_result=evidence,
                provider_succeeded_at=now,
                updated_at=now,
            )
            write_note_receipt(app, current)
            db.commit()
            return None
    if app is not None:
        archive_orphaned_note_result(
            app,
            plan=plan,
            provider_called=True,
            provider_succeeded=True,
        )
        db.commit()
    else:
        db.rollback()
    return {
        "status": "manual_reconciliation_required",
        "application_id": plan.application_id,
        "failed": 1,
    }


def finish_ats_note_delivery(
    db: Session,
    *,
    plan: AtsNoteProviderPlan,
    actor_type: str,
    actor_id: int | None,
    failure: AtsNoteProviderFailure | None = None,
) -> dict[str, Any]:
    """Terminalize only the exact claimed receipt and append its audit event."""

    from ..domains.assessments_runtime.pipeline_service import append_application_event

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == plan.application_id,
            CandidateApplication.organization_id == plan.organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    current = note_receipt(app) if app is not None else None
    expected_status = (
        "provider_succeeded" if failure is None else "provider_call_started"
    )
    if (
        app is None
        or current is None
        or not note_receipt_matches(current, plan)
        or str(current.get("status") or "") != expected_status
    ):
        if (
            failure is None
            and current is not None
            and note_receipt_matches(current, plan)
            and str(current.get("status") or "") == "confirmed"
        ):
            db.rollback()
            return {
                "status": "already_completed",
                "application_id": plan.application_id,
            }
        if app is not None:
            archive_orphaned_note_result(
                app,
                plan=plan,
                provider_called=(True if failure is None else failure.provider_called),
                provider_succeeded=(True if failure is None else None),
                failure_code=failure.code if failure is not None else None,
            )
            db.commit()
        else:
            db.rollback()
        return {
            "status": "manual_reconciliation_required",
            "application_id": plan.application_id,
            "failed": 1,
        }
    now = note_receipt_now()
    uncertain = failure is not None and failure.provider_called is not False
    status = (
        "confirmed"
        if failure is None
        else ("manual_reconciliation_required" if uncertain else "failed")
    )
    current.update(
        status=status,
        provider_called=(True if failure is None else failure.provider_called),
        provider_succeeded=(
            True
            if failure is None
            else (False if failure.provider_called is False else None)
        ),
        provider_outcome_uncertain=uncertain,
        manual_reconciliation_required=uncertain,
        updated_at=now,
    )
    if failure is None:
        current["confirmed_at"] = now
    else:
        current["failure_code"] = failure.code
        current["failed_at"] = now
    write_note_receipt(app, current)
    append_application_event(
        db,
        app=app,
        event_type=(
            f"{plan.provider}_note_posted"
            if failure is None
            else (
                "ats_note_manual_reconciliation_required"
                if uncertain
                else f"{plan.provider}_note_failed"
            )
        ),
        actor_type=actor_type,
        actor_id=actor_id,
        reason=(
            f"Note posted to {plan.provider.title()}"
            if failure is None
            else failure.message
        ),
        metadata={
            "operation_id": plan.operation_id,
            "ats_provider": plan.provider,
            "provider_target_id": plan.provider_target_id,
            "body_sha256": plan.body_sha256,
            "provider_called": current.get("provider_called"),
            "attempts": current.get("attempts"),
        },
        idempotency_key=f"{plan.operation_id}:terminal"[:200],
    )
    db.commit()
    return {
        "status": "ok" if failure is None else status,
        "receipt_status": status,
        "application_id": plan.application_id,
        "failed": 0 if failure is None else 1,
        "provider_called": True if failure is None else failure.provider_called,
    }


__all__ = [
    "ATS_NOTE_WRITEBACK_HISTORY_KEY",
    "ATS_NOTE_WRITEBACK_KEY",
    "AtsNoteProviderFailure",
    "AtsNoteProviderPlan",
    "checkpoint_ats_note_provider_success",
    "finish_ats_note_delivery",
    "ensure_note_operation_payload",
    "note_body_fingerprint",
    "perform_ats_note_provider_call",
    "prepare_ats_note_delivery",
]
