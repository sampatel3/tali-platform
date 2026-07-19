"""Execute exact Workable result receipts without importing task/outbox layers."""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..components.assessments.result_delivery_contracts import (
    DELIVERY_CONFIRMED,
    DELIVERY_DISPATCHING,
    DELIVERY_FAILED,
    DELIVERY_PROVIDER_STARTED,
    DELIVERY_RECONCILIATION_REQUIRED,
    DELIVERY_RETRY_WAIT,
    DELIVERABLE_STATUSES as _DELIVERABLE_STATUSES,
    MAX_PROVIDER_ATTEMPTS as _MAX_PROVIDER_ATTEMPTS,
    PROVIDER_STALE_AFTER as _PROVIDER_STALE_AFTER,
    AssessmentResultDispatch,
    ProviderPlan as _ProviderPlan,
    as_utc as _as_utc,
    current_context as _current_context,
    fingerprint as _fingerprint,
    iso as _iso,
    load_locked as _load_locked,
    now as _now,
    provider_retry_delay as _provider_retry_delay,
    receipt_copy as _receipt_copy,
    receipt_counter as _receipt_counter,
    receipt_hash_history as _receipt_hash_history,
    record_unavailable_context as _record_unavailable_context,
    safe_error_code as _safe_error_code,
    valid_receipt as _valid_receipt,
    write_receipt as _write_receipt,
)
from ..platform.config import settings
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.assessment_result_workable_delivery")


def _mark_pre_provider_failure(
    db: Session,
    plan: _ProviderPlan,
    *,
    error_code: str,
) -> str:
    row = _load_locked(
        db,
        assessment_id=plan.dispatch.assessment_id,
        organization_id=plan.dispatch.organization_id,
    )
    if row is None:
        db.rollback()
        return "stale"
    receipt = _receipt_copy(row.workable_result_delivery_receipt)
    if (
        not _valid_receipt(
            receipt,
            dispatch=plan.dispatch,
            expected_status=str(row.workable_result_delivery_status or ""),
        )
        or str(receipt.get("status") or "") != DELIVERY_PROVIDER_STARTED
    ):
        db.rollback()
        return "stale"
    receipt.update(
        provider_called=False,
        provider_outcome_uncertain=False,
        last_error_code=error_code,
    )
    if plan.attempt < _MAX_PROVIDER_ATTEMPTS:
        _write_receipt(
            row,
            receipt,
            status=DELIVERY_RETRY_WAIT,
            next_attempt_at=_now() + _provider_retry_delay(plan.attempt),
        )
        outcome = DELIVERY_RETRY_WAIT
    else:
        _write_receipt(row, receipt, status=DELIVERY_FAILED)
        outcome = DELIVERY_FAILED
    db.commit()
    return outcome


def _claim_provider_call(
    db: Session,
    dispatch: AssessmentResultDispatch,
    *,
    settings_obj: Any = settings,
) -> tuple[_ProviderPlan | None, str]:
    row = _load_locked(
        db,
        assessment_id=dispatch.assessment_id,
        organization_id=dispatch.organization_id,
    )
    if row is None:
        db.rollback()
        return None, "not_found"
    if bool(getattr(row, "is_voided", False)):
        db.rollback()
        return None, "assessment_voided"
    receipt = _receipt_copy(row.workable_result_delivery_receipt)
    if not _valid_receipt(
        receipt,
        dispatch=dispatch,
        expected_status=str(row.workable_result_delivery_status or ""),
    ):
        receipt.update(
            provider_outcome_uncertain=True,
            last_error_code="delivery_receipt_invalid",
        )
        _write_receipt(row, receipt, status=DELIVERY_RECONCILIATION_REQUIRED)
        db.commit()
        return None, DELIVERY_RECONCILIATION_REQUIRED
    if bool(row.posted_to_workable):
        _write_receipt(row, receipt, status=DELIVERY_CONFIRMED)
        db.commit()
        return None, DELIVERY_CONFIRMED
    status = str(receipt.get("status") or "")
    if status in {DELIVERY_PROVIDER_STARTED, DELIVERY_RECONCILIATION_REQUIRED}:
        if status == DELIVERY_PROVIDER_STARTED:
            claimed_at = _as_utc(row.workable_result_delivery_claimed_at)
            if claimed_at is not None and claimed_at > _now() - _PROVIDER_STALE_AFTER:
                db.rollback()
                return None, "provider_call_in_progress"
            receipt.update(
                provider_outcome_uncertain=True,
                last_error_code="provider_call_already_started",
            )
            _write_receipt(
                row,
                receipt,
                status=DELIVERY_RECONCILIATION_REQUIRED,
            )
            db.commit()
        else:
            db.rollback()
        return None, DELIVERY_RECONCILIATION_REQUIRED
    if status not in _DELIVERABLE_STATUSES | {DELIVERY_DISPATCHING}:
        db.rollback()
        return None, status or "not_deliverable"
    due = _as_utc(row.workable_result_delivery_next_attempt_at)
    if status == DELIVERY_RETRY_WAIT and due is not None and due > _now():
        db.rollback()
        return None, "not_due"

    # Reaching the worker proves the latest broker handoff succeeded. Keep
    # publish_attempts as a consecutive-failure counter so healthy repeated
    # configuration waits never exhaust broker attempts.
    receipt["publish_attempts"] = 0
    stored_intent = receipt.get("intent")
    stored_results_url = (
        str(stored_intent.get("assessment_data", {}).get("results_url") or "")
        if isinstance(stored_intent, dict)
        else ""
    )
    context, reason = _current_context(
        db,
        row,
        results_url=stored_results_url or None,
        settings_obj=settings_obj,
    )
    if context is None or reason != "ready":
        terminal_status = _record_unavailable_context(
            row,
            receipt,
            reason=reason,
        )
        db.commit()
        return None, terminal_status
    if context.intent != stored_intent:
        history = _receipt_hash_history(receipt)
        history.append(str(receipt.get("intent_sha256") or ""))
        receipt["prior_intent_sha256"] = history[-5:]
        receipt["intent"] = context.intent
        receipt["intent_sha256"] = _fingerprint(context.intent)
        receipt["intent_revisions"] = (
            _receipt_counter(receipt, "intent_revisions") + 1
        )
        stored_intent = context.intent
    attempt = _receipt_counter(receipt, "provider_attempts") + 1
    if attempt > _MAX_PROVIDER_ATTEMPTS:
        receipt["last_error_code"] = "provider_attempts_exhausted"
        _write_receipt(row, receipt, status=DELIVERY_FAILED)
        db.commit()
        return None, DELIVERY_FAILED
    receipt.update(
        provider_attempts=attempt,
        provider_called=True,
        provider_succeeded=False,
        provider_outcome_uncertain=False,
        provider_call_started_at=_iso(),
        last_error_code=None,
    )
    _write_receipt(
        row,
        receipt,
        status=DELIVERY_PROVIDER_STARTED,
        claimed_at=_now(),
    )
    db.commit()
    intent = context.intent
    return (
        _ProviderPlan(
            dispatch=dispatch,
            subdomain=str(intent["subdomain"]),
            candidate_id=str(intent["candidate_id"]),
            member_id=str(intent["member_id"]),
            assessment_data=dict(intent["assessment_data"]),
            attempt=attempt,
            access_token=context.access_token,
        ),
        "claimed",
    )


def _finish_success(db: Session, plan: _ProviderPlan) -> str:
    row = _load_locked(
        db,
        assessment_id=plan.dispatch.assessment_id,
        organization_id=plan.dispatch.organization_id,
    )
    if row is None:
        db.rollback()
        return "stale"
    receipt = _receipt_copy(row.workable_result_delivery_receipt)
    if (
        not _valid_receipt(
            receipt,
            dispatch=plan.dispatch,
            expected_status=str(row.workable_result_delivery_status or ""),
        )
        or str(receipt.get("status") or "") != DELIVERY_PROVIDER_STARTED
        or _receipt_counter(receipt, "provider_attempts") != plan.attempt
    ):
        db.rollback()
        return "stale"
    timestamp = _now()
    receipt.update(
        provider_succeeded=True,
        provider_outcome_uncertain=False,
        provider_confirmed_at=_iso(timestamp),
        last_error_code=None,
    )
    row.posted_to_workable = True
    row.posted_to_workable_at = timestamp
    _write_receipt(row, receipt, status=DELIVERY_CONFIRMED)
    db.commit()
    return DELIVERY_CONFIRMED


def _finish_provider_failure(
    db: Session,
    plan: _ProviderPlan,
    *,
    error_code: str,
    status_code: int | None,
) -> str:
    row = _load_locked(
        db,
        assessment_id=plan.dispatch.assessment_id,
        organization_id=plan.dispatch.organization_id,
    )
    if row is None:
        db.rollback()
        return "stale"
    receipt = _receipt_copy(row.workable_result_delivery_receipt)
    if (
        not _valid_receipt(
            receipt,
            dispatch=plan.dispatch,
            expected_status=str(row.workable_result_delivery_status or ""),
        )
        or str(receipt.get("status") or "") != DELIVERY_PROVIDER_STARTED
        or _receipt_counter(receipt, "provider_attempts") != plan.attempt
    ):
        db.rollback()
        return "stale"
    code = _safe_error_code(error_code)
    receipt["last_error_code"] = code
    receipt["last_status_code"] = status_code
    definitive_retry = code == "workable_rate_limited" and status_code == 429
    definitive_failure = status_code in {400, 401, 403, 404, 409, 410, 422}
    if definitive_retry and plan.attempt < _MAX_PROVIDER_ATTEMPTS:
        receipt["provider_outcome_uncertain"] = False
        _write_receipt(
            row,
            receipt,
            status=DELIVERY_RETRY_WAIT,
            next_attempt_at=_now() + _provider_retry_delay(plan.attempt),
        )
        outcome = DELIVERY_RETRY_WAIT
    elif definitive_failure:
        receipt["provider_outcome_uncertain"] = False
        _write_receipt(row, receipt, status=DELIVERY_FAILED)
        outcome = DELIVERY_FAILED
    else:
        receipt["provider_outcome_uncertain"] = True
        _write_receipt(
            row,
            receipt,
            status=DELIVERY_RECONCILIATION_REQUIRED,
        )
        outcome = DELIVERY_RECONCILIATION_REQUIRED
    db.commit()
    return outcome


def deliver_assessment_result(
    *,
    assessment_id: int,
    organization_id: int,
    operation_id: str,
    settings_obj: Any = settings,
    adapter_builder: Callable[..., Any] | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    dispatch = AssessmentResultDispatch(
        assessment_id=int(assessment_id),
        organization_id=int(organization_id),
        operation_id=str(operation_id),
    )
    with session_factory() as db:
        plan, disposition = _claim_provider_call(
            db,
            dispatch,
            settings_obj=settings_obj,
        )
    if plan is None:
        return {"status": disposition}
    if adapter_builder is None:
        from ..domains.integrations_notifications.adapters import (
            build_workable_adapter,
        )

        adapter_builder = build_workable_adapter
    try:
        adapter = adapter_builder(
            access_token=plan.access_token,
            subdomain=plan.subdomain,
        )
    except Exception as exc:
        logger.warning(
            "Assessment result adapter unavailable assessment_id=%s error_type=%s",
            plan.dispatch.assessment_id,
            type(exc).__name__,
        )
        with session_factory() as db:
            outcome = _mark_pre_provider_failure(
                db,
                plan,
                error_code="workable_credential_unavailable",
            )
        return {"status": outcome}
    try:
        result = adapter.post_assessment_result(
            candidate_id=plan.candidate_id,
            member_id=plan.member_id,
            assessment_data=plan.assessment_data,
        )
    except Exception as exc:
        logger.warning(
            "Assessment result provider outcome uncertain assessment_id=%s error_type=%s",
            plan.dispatch.assessment_id,
            type(exc).__name__,
        )
        with session_factory() as db:
            outcome = _finish_provider_failure(
                db,
                plan,
                error_code="workable_network_error",
                status_code=None,
            )
        return {"status": outcome}
    if isinstance(result, dict) and result.get("success") is True:
        with session_factory() as db:
            outcome = _finish_success(db, plan)
        return {"status": outcome, "success": outcome == DELIVERY_CONFIRMED}
    result = result if isinstance(result, dict) else {}
    raw_status = result.get("status_code")
    try:
        status_code = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status_code = None
    with session_factory() as db:
        outcome = _finish_provider_failure(
            db,
            plan,
            error_code=_safe_error_code(
                result.get("error_code") or result.get("error")
            ),
            status_code=status_code,
        )
    return {"status": outcome, "success": False}


def run_assessment_result_delivery_task(
    *,
    assessment_id: int | None = None,
    organization_id: int | None = None,
    operation_id: str | None = None,
    access_token: str | None = None,
    subdomain: str | None = None,
    candidate_id: str | None = None,
    assessment_data: dict[str, Any] | None = None,
    member_id: str | None = None,
    request_id: str | None = None,
    settings_obj: Any = settings,
    adapter_builder: Callable[..., Any] | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    """Consume identity-only calls and safely fence rolling-deploy payloads."""

    del access_token, member_id
    if assessment_id is not None or organization_id is not None or operation_id:
        if assessment_id is None or organization_id is None or not operation_id:
            return {"status": "invalid_delivery_identity", "success": False}
        return deliver_assessment_result(
            assessment_id=int(assessment_id),
            organization_id=int(organization_id),
            operation_id=str(operation_id),
            settings_obj=settings_obj,
            adapter_builder=adapter_builder,
            session_factory=session_factory,
        )
    del request_id, settings_obj, adapter_builder
    from .assessment_result_legacy_compatibility import (
        fence_legacy_assessment_result_payload,
    )

    return fence_legacy_assessment_result_payload(
        subdomain=subdomain,
        candidate_id=candidate_id,
        assessment_data=assessment_data,
        session_factory=session_factory,
    )


__all__ = [
    "deliver_assessment_result",
    "run_assessment_result_delivery_task",
]
