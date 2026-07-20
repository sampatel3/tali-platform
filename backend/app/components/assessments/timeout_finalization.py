"""Failure reconciliation for server-side assessment timeout submission."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus
from ...models.task import Task
from .repository import append_assessment_timeline_event
from .submission_runtime import build_submission_receipt

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    AssessmentStatus.COMPLETED,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
}


def _terminal_submission_outcome(
    assessment: Assessment,
    task: Task,
    db: Session,
) -> Dict[str, Any]:
    """Accept a racing terminal row only when its artifact is durable."""
    assessment_id = int(assessment.id)
    try:
        build_submission_receipt(assessment, task)
    except RuntimeError:
        assessment.scoring_failed = True
        append_assessment_timeline_event(
            assessment,
            "auto_submit_timeout_capture_failed",
            {"error": "terminal_submission_receipt_unavailable"},
        )
        db.commit()
        logger.exception(
            "Timed-out finalize: terminal row had no durable receipt assessment_id=%s",
            assessment_id,
        )
        return {
            "status": "capture_failed",
            "assessment_id": assessment_id,
            "scoring_failed": True,
        }
    db.rollback()
    return {"status": "already_submitted", "assessment_id": assessment_id}


def _persist_live_capture_failure(
    assessment: Assessment,
    task: Task,
    db: Session,
    exc: HTTPException,
) -> Dict[str, Any]:
    """Record a non-conflict failure without racing a terminal acceptance."""
    assessment_id = int(assessment.id)
    db.rollback()
    current = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if current is not None and current.status in _TERMINAL_STATUSES:
        return _terminal_submission_outcome(current, task, db)
    if current is not None and current.status == AssessmentStatus.IN_PROGRESS:
        current.scoring_failed = True
        append_assessment_timeline_event(
            current,
            "auto_submit_timeout_capture_failed",
            {"error": str(getattr(exc, "detail", exc))[:500]},
        )
        db.commit()
    else:
        db.rollback()
    return {
        "status": "capture_failed",
        "assessment_id": assessment_id,
        "scoring_failed": True,
    }


def reconcile_timeout_submission_http_error(
    assessment: Assessment,
    task: Task,
    db: Session,
    exc: HTTPException,
) -> Dict[str, Any] | None:
    """Resolve an HTTP failure after a timeout submission attempt.

    A racing terminal submission is accepted only when its immutable receipt
    can be rebuilt. Live or ambiguous states remain capture failures so the
    timeout sweep can retry them instead of claiming that work was submitted.
    ``None`` means the caller may continue its post-acceptance failure path.
    """
    db.rollback()
    db.refresh(assessment)
    current_status = assessment.status
    if exc.status_code == 409 and current_status in _TERMINAL_STATUSES:
        return _terminal_submission_outcome(assessment, task, db)
    if current_status == AssessmentStatus.IN_PROGRESS:
        if exc.status_code != 409:
            return _persist_live_capture_failure(assessment, task, db, exc)
        # A 409 while the row is live means another save, Claude turn, or
        # candidate submit may own the runtime lease. Do not write a stale copy
        # of the JSON timeline (or poison scoring state) while that winner can
        # still commit. Re-open the transaction once so a terminal winner that
        # landed after the first read can be acknowledged by its durable
        # receipt; otherwise the next timeout sweep safely retries.
        db.rollback()
        db.refresh(assessment)
        if assessment.status in _TERMINAL_STATUSES:
            return _terminal_submission_outcome(assessment, task, db)
        logger.warning(
            "Timed-out finalize: live capture remains retryable assessment_id=%s status=%s detail=%s",
            assessment.id,
            assessment.status,
            getattr(exc, "detail", exc),
        )
        return {
            "status": "capture_failed",
            "assessment_id": assessment.id,
            # Attempt-level compatibility for sweep counters; unlike a durable
            # scoring failure, a contested live row is deliberately unchanged.
            "scoring_failed": True,
        }
    if exc.status_code == 409:
        logger.warning(
            "Timed-out finalize: non-terminal 409 assessment_id=%s status=%s detail=%s",
            assessment.id,
            current_status,
            getattr(exc, "detail", exc),
        )
        return {
            "status": "capture_failed",
            "assessment_id": assessment.id,
            "scoring_failed": True,
        }
    return None
