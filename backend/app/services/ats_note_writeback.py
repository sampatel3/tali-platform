"""Exact, non-replayable ATS note delivery with a durable ambiguity fence."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .ats_note_claim import (
    ensure_note_operation_payload,
    lock_ats_note_provider_scope,
    note_body_fingerprint,
    prepare_ats_note_delivery,
)
from .ats_note_audit import (
    ats_note_event_key,
    confirmed_note_metadata,
)
from .ats_note_provider import (
    AtsNoteProviderFailure,
    AtsNoteProviderPlan,
    perform_ats_note_provider_call,
)
from .ats_note_receipt import (
    ATS_NOTE_WRITEBACK_KEY,
    note_receipt,
    note_receipt_matches,
    note_receipt_now,
    write_note_receipt,
)


def _append_note_event(
    db: Session,
    *,
    app: CandidateApplication,
    plan: AtsNoteProviderPlan,
    event_type: str,
    actor_type: str,
    actor_id: int | None,
    reason: str,
    note_intent_sha256: str,
    outcome: str,
    attempt: int | None = None,
    provider_called: bool | None = None,
    failure_code: str | None = None,
) -> None:
    from ..domains.assessments_runtime.pipeline_service import append_application_event

    metadata = confirmed_note_metadata(
        plan,
        note_intent_sha256=note_intent_sha256,
    )
    metadata.update(
        provider_called=provider_called,
        attempts=attempt,
        failure_code=failure_code,
    )
    append_application_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        metadata=metadata,
        idempotency_key=ats_note_event_key(
            plan.operation_id,
            outcome,
            attempt=attempt if outcome == "failed" else None,
        ),
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
        _append_note_event(
            db,
            app=app,
            plan=plan,
            event_type="ats_note_manual_reconciliation_required",
            actor_type="system",
            actor_id=None,
            reason="ATS note provider success could not be matched to its claim",
            note_intent_sha256="",
            outcome="reconciliation",
            provider_called=True,
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
            definite_pre_call_failure = (
                failure is not None and failure.provider_called is False
            )
            _append_note_event(
                db,
                app=app,
                plan=plan,
                event_type=(
                    f"{plan.provider}_note_failed"
                    if definite_pre_call_failure
                    else "ats_note_manual_reconciliation_required"
                ),
                actor_type=actor_type,
                actor_id=actor_id,
                reason=(
                    failure.message
                    if failure is not None
                    else "ATS note provider success could not be matched to its claim"
                ),
                note_intent_sha256="",
                outcome=("failed" if definite_pre_call_failure else "reconciliation"),
                attempt=1 if definite_pre_call_failure else None,
                provider_called=(
                    True if failure is None else failure.provider_called
                ),
                failure_code=failure.code if failure is not None else None,
            )
            db.commit()
        else:
            db.rollback()
        mismatch_status = (
            "failed"
            if failure is not None and failure.provider_called is False
            else "manual_reconciliation_required"
        )
        return {
            "status": mismatch_status,
            "application_id": plan.application_id,
            "failed": 1,
            "provider_called": failure.provider_called if failure else True,
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
        current["retriable"] = failure.retriable
        current["failed_at"] = now
    write_note_receipt(app, current)
    attempts = max(1, int(current.get("attempts") or 1))
    _append_note_event(
        db,
        app=app,
        plan=plan,
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
        note_intent_sha256=str(current.get("note_intent_sha256") or ""),
        outcome="confirmed" if failure is None else "failed",
        attempt=attempts,
        provider_called=current.get("provider_called"),
        failure_code=failure.code if failure is not None else None,
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
    "ATS_NOTE_WRITEBACK_KEY",
    "AtsNoteProviderFailure",
    "AtsNoteProviderPlan",
    "checkpoint_ats_note_provider_success",
    "finish_ats_note_delivery",
    "ensure_note_operation_payload",
    "lock_ats_note_provider_scope",
    "note_body_fingerprint",
    "perform_ats_note_provider_call",
    "prepare_ats_note_delivery",
]
