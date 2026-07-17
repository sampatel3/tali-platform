"""Broker publication and bounded recovery for assessment-result receipts."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus
from ...platform.config import settings
from ...platform.database import SessionLocal
from .result_delivery_contracts import (
    DELIVERY_CANCELLED,
    DELIVERY_CONFIRMED,
    DELIVERY_DISPATCH_FAILED,
    DELIVERY_DISPATCHING,
    DELIVERY_PENDING,
    DELIVERY_PROVIDER_STARTED,
    DELIVERY_RECONCILIATION_REQUIRED,
    DELIVERY_RETRY_WAIT,
    DELIVERY_SUPERSEDED,
    DELIVERABLE_STATUSES,
    DISPATCH_STALE_AFTER,
    MAX_PUBLISH_ATTEMPTS,
    PROVIDER_STALE_AFTER,
    AssessmentResultDispatch,
    as_utc,
    current_context,
    fingerprint,
    iso,
    load_locked,
    now,
    provisional_intent,
    receipt_copy,
    receipt_counter,
    receipt_hash_history,
    safe_request_id,
    valid_receipt,
    write_receipt,
)
from .result_delivery_legacy_inventory import (
    classify_legacy_assessment_result_delivery,
)


logger = logging.getLogger("taali.assessment_result_workable_delivery")


def _default_delivery_task() -> Any:
    """Resolve the live Celery publisher while preserving its ``.delay`` seam.

    The dedicated task consumes the provider executor, which imports neither
    this outbox nor the broad assessment task module. The dependency graph is
    therefore acyclic; the local import merely avoids loading Celery on paths
    that only attach or inspect receipts.
    """

    from ...tasks.assessment_result_delivery_tasks import (
        post_results_to_workable,
    )

    return post_results_to_workable


def attach_assessment_result_delivery_receipt(
    db: Session,
    row: Assessment,
    *,
    request_id: str | None = None,
    settings_obj: Any = settings,
) -> AssessmentResultDispatch | None:
    """Attach exact delivery authority without controlling the transaction."""

    if bool(getattr(row, "is_voided", False)):
        return None
    raw_receipt = row.workable_result_delivery_receipt
    existing = receipt_copy(raw_receipt) if raw_receipt is not None else None
    if bool(row.posted_to_workable):
        if existing is not None:
            write_receipt(row, existing, status=DELIVERY_CONFIRMED)
        return None
    if existing is not None:
        dispatch = AssessmentResultDispatch(
            assessment_id=int(row.id),
            organization_id=int(row.organization_id),
            operation_id=str(existing.get("operation_id") or ""),
        )
        if not valid_receipt(
            existing,
            dispatch=dispatch,
            expected_status=str(row.workable_result_delivery_status or ""),
        ):
            existing["provider_outcome_uncertain"] = True
            existing["last_error_code"] = "delivery_receipt_invalid"
            write_receipt(
                row,
                existing,
                status=DELIVERY_RECONCILIATION_REQUIRED,
            )
            return None
        status = str(existing.get("status") or "")
        if status in DELIVERABLE_STATUSES and not bool(existing.get("provider_called")):
            context, _reason = current_context(db, row, settings_obj=settings_obj)
            if context is not None and context.intent != existing.get("intent"):
                history = receipt_hash_history(existing)
                history.append(str(existing.get("intent_sha256") or ""))
                existing["prior_intent_sha256"] = history[-5:]
                existing["intent"] = context.intent
                existing["intent_sha256"] = fingerprint(context.intent)
                existing["intent_revisions"] = (
                    receipt_counter(existing, "intent_revisions") + 1
                )
                write_receipt(row, existing, status=status)
        return dispatch if status in DELIVERABLE_STATUSES else None

    context, reason = current_context(db, row, settings_obj=settings_obj)
    operation_id = uuid.uuid4().hex
    timestamp = now()
    intent = (
        context.intent
        if context is not None
        else provisional_intent(row, settings_obj=settings_obj)
    )
    if reason == "ready":
        receipt_status = DELIVERY_PENDING
    elif reason in {
        "workable_credential_missing",
        "workable_subdomain_missing",
        "workable_actor_missing",
        "workable_candidate_missing",
    }:
        receipt_status = DELIVERY_RETRY_WAIT
    elif reason in {
        "workable_disabled",
        "writeback_disabled",
        "workable_disconnected",
    }:
        receipt_status = DELIVERY_CANCELLED
    else:
        receipt_status = DELIVERY_SUPERSEDED
    receipt = {
        "version": 1,
        "operation_id": operation_id,
        "intent": intent,
        "intent_sha256": fingerprint(intent),
        "status": receipt_status,
        "provider_attempts": 0,
        "publish_attempts": 0,
        "provider_called": False,
        "provider_succeeded": False,
        "provider_outcome_uncertain": False,
        "request_id": safe_request_id(request_id),
        "created_at": iso(timestamp),
        "updated_at": iso(timestamp),
    }
    if reason != "ready":
        receipt["last_error_code"] = reason
    dispatch = AssessmentResultDispatch(
        assessment_id=int(row.id),
        organization_id=int(row.organization_id),
        operation_id=operation_id,
    )
    write_receipt(
        row,
        receipt,
        status=receipt_status,
        next_attempt_at=(timestamp if receipt_status == DELIVERY_RETRY_WAIT else None),
    )
    return dispatch if receipt_status in DELIVERABLE_STATUSES else None


def authorize_assessment_result_delivery(
    db: Session,
    *,
    assessment_id: int,
    organization_id: int,
    request_id: str | None = None,
    settings_obj: Any = settings,
) -> AssessmentResultDispatch | None:
    """Persist one exact provider intent before any broker/provider call."""

    row = load_locked(
        db,
        assessment_id=int(assessment_id),
        organization_id=int(organization_id),
    )
    if row is None:
        db.rollback()
        return None
    dispatch = attach_assessment_result_delivery_receipt(
        db,
        row,
        request_id=request_id,
        settings_obj=settings_obj,
    )
    db.commit()
    return dispatch


def _publish_retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(1800, 30 * (2 ** max(0, attempt - 1))))


def _prepare_publish(db: Session, dispatch: AssessmentResultDispatch) -> bool:
    row = load_locked(
        db,
        assessment_id=dispatch.assessment_id,
        organization_id=dispatch.organization_id,
    )
    if row is None:
        db.rollback()
        return False
    if bool(getattr(row, "is_voided", False)):
        db.rollback()
        return False
    receipt = receipt_copy(row.workable_result_delivery_receipt)
    if not valid_receipt(
        receipt,
        dispatch=dispatch,
        expected_status=str(row.workable_result_delivery_status or ""),
    ):
        receipt.update(
            provider_outcome_uncertain=True,
            last_error_code="delivery_receipt_invalid",
        )
        write_receipt(
            row,
            receipt,
            status=DELIVERY_RECONCILIATION_REQUIRED,
        )
        db.commit()
        return False
    timestamp = now()
    status = str(receipt.get("status") or "")
    due = as_utc(row.workable_result_delivery_next_attempt_at)
    claimed = as_utc(row.workable_result_delivery_claimed_at)
    if status in {DELIVERY_PENDING, DELIVERY_RETRY_WAIT}:
        if due is not None and due > timestamp:
            db.rollback()
            return False
    elif status == DELIVERY_DISPATCHING:
        if claimed is not None and claimed > timestamp - DISPATCH_STALE_AFTER:
            db.rollback()
            return False
    else:
        db.rollback()
        return False
    publish_attempts = receipt_counter(receipt, "publish_attempts") + 1
    receipt["publish_attempts"] = publish_attempts
    if publish_attempts > MAX_PUBLISH_ATTEMPTS:
        receipt["last_error_code"] = "broker_publish_exhausted"
        write_receipt(row, receipt, status=DELIVERY_DISPATCH_FAILED)
        db.commit()
        return False
    write_receipt(row, receipt, status=DELIVERY_DISPATCHING, claimed_at=timestamp)
    db.commit()
    return True


def _record_publish_failure(
    db: Session, dispatch: AssessmentResultDispatch
) -> None:
    row = load_locked(
        db,
        assessment_id=dispatch.assessment_id,
        organization_id=dispatch.organization_id,
    )
    if row is None:
        db.rollback()
        return
    receipt = receipt_copy(row.workable_result_delivery_receipt)
    if (
        not valid_receipt(
            receipt,
            dispatch=dispatch,
            expected_status=str(row.workable_result_delivery_status or ""),
        )
        or str(receipt.get("status") or "") != DELIVERY_DISPATCHING
    ):
        db.rollback()
        return
    attempts = receipt_counter(receipt, "publish_attempts")
    receipt["last_error_code"] = "broker_publish_failed"
    if attempts >= MAX_PUBLISH_ATTEMPTS:
        write_receipt(row, receipt, status=DELIVERY_DISPATCH_FAILED)
    else:
        write_receipt(
            row,
            receipt,
            status=DELIVERY_PENDING,
            next_attempt_at=now() + _publish_retry_delay(attempts),
        )
    db.commit()


def publish_assessment_result_delivery(
    dispatch: AssessmentResultDispatch,
    *,
    task: Any | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> str:
    """Publish only primitive receipt identity; retain recovery on failure."""

    with session_factory() as db:
        if not _prepare_publish(db, dispatch):
            return "not_due"
    if task is None:
        task = _default_delivery_task()
    try:
        task.delay(
            assessment_id=dispatch.assessment_id,
            organization_id=dispatch.organization_id,
            operation_id=dispatch.operation_id,
        )
        return "published"
    except Exception as exc:
        logger.warning(
            "Assessment result broker publish failed assessment_id=%s error_type=%s",
            dispatch.assessment_id,
            type(exc).__name__,
        )
        with session_factory() as db:
            _record_publish_failure(db, dispatch)
        return "publish_failed"


def enqueue_assessment_result_delivery(
    *,
    assessment_id: int,
    organization_id: int,
    request_id: str | None = None,
    settings_obj: Any = settings,
    task: Any | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    with session_factory() as db:
        dispatch = authorize_assessment_result_delivery(
            db,
            assessment_id=int(assessment_id),
            organization_id=int(organization_id),
            request_id=request_id,
            settings_obj=settings_obj,
        )
    if dispatch is None:
        return {"status": "not_eligible"}
    return {
        "status": publish_assessment_result_delivery(
            dispatch,
            task=task,
            session_factory=session_factory,
        ),
        "operation_id": dispatch.operation_id,
    }


def sweep_assessment_result_deliveries(
    *,
    limit: int = 100,
    task: Any | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """Recover bounded broker loss; fence stale provider calls as ambiguous."""

    timestamp = now()
    dispatch_cutoff = timestamp - DISPATCH_STALE_AFTER
    provider_cutoff = timestamp - PROVIDER_STALE_AFTER
    bounded_limit = max(0, min(int(limit), 500))
    if bounded_limit == 0:
        return {"scanned": 0, "published": 0, "reconciliation_required": 0}
    with session_factory() as db:
        rows = (
            db.query(Assessment)
            .filter(
                Assessment.is_voided.is_(False),
                or_(
                    and_(
                        Assessment.workable_result_delivery_status.in_(
                            {DELIVERY_PENDING, DELIVERY_RETRY_WAIT}
                        ),
                        or_(
                            Assessment.workable_result_delivery_next_attempt_at.is_(
                                None
                            ),
                            Assessment.workable_result_delivery_next_attempt_at
                            <= timestamp,
                        ),
                    ),
                    and_(
                        Assessment.workable_result_delivery_status
                        == DELIVERY_DISPATCHING,
                        or_(
                            Assessment.workable_result_delivery_claimed_at.is_(None),
                            Assessment.workable_result_delivery_claimed_at
                            <= dispatch_cutoff,
                        ),
                    ),
                    and_(
                        Assessment.workable_result_delivery_status
                        == DELIVERY_PROVIDER_STARTED,
                        or_(
                            Assessment.workable_result_delivery_claimed_at.is_(None),
                            Assessment.workable_result_delivery_claimed_at
                            <= provider_cutoff,
                        ),
                    ),
                    and_(
                        Assessment.workable_result_delivery_status.is_(None),
                        Assessment.workable_result_delivery_receipt.is_(None),
                        Assessment.posted_to_workable.is_(False),
                        Assessment.workable_candidate_id.isnot(None),
                        Assessment.workable_candidate_id != "",
                        Assessment.status.in_(
                            {
                                AssessmentStatus.COMPLETED,
                                AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                            }
                        ),
                    ),
                )
            )
            .order_by(Assessment.id.asc())
            .with_for_update(skip_locked=True)
            .limit(bounded_limit)
            .all()
        )
        dispatches: list[AssessmentResultDispatch] = []
        reconciliations = 0
        for row in rows:
            if row.workable_result_delivery_status is None:
                if classify_legacy_assessment_result_delivery(
                    row, timestamp=timestamp
                ):
                    reconciliations += 1
                continue
            receipt = receipt_copy(row.workable_result_delivery_receipt)
            dispatch = AssessmentResultDispatch(
                assessment_id=int(row.id),
                organization_id=int(row.organization_id),
                operation_id=str(receipt.get("operation_id") or ""),
            )
            if not valid_receipt(
                receipt,
                dispatch=dispatch,
                expected_status=str(row.workable_result_delivery_status or ""),
            ):
                receipt.update(
                    provider_outcome_uncertain=True,
                    last_error_code="delivery_receipt_invalid",
                )
                write_receipt(
                    row,
                    receipt,
                    status=DELIVERY_RECONCILIATION_REQUIRED,
                )
                reconciliations += 1
            elif row.workable_result_delivery_status == DELIVERY_PROVIDER_STARTED:
                receipt.update(
                    provider_outcome_uncertain=True,
                    last_error_code="provider_worker_stale_after_call_started",
                )
                write_receipt(
                    row,
                    receipt,
                    status=DELIVERY_RECONCILIATION_REQUIRED,
                )
                reconciliations += 1
            else:
                dispatches.append(dispatch)
        db.commit()
    published = sum(
        publish_assessment_result_delivery(
            dispatch,
            task=task,
            session_factory=session_factory,
        )
        == "published"
        for dispatch in dispatches
    )
    return {
        "scanned": len(rows),
        "published": published,
        "reconciliation_required": reconciliations,
    }
