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
    if exc.status_code == 409 and current_status in {
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    }:
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
                "Timed-out finalize: terminal 409 had no durable receipt assessment_id=%s",
                assessment.id,
            )
            return {
                "status": "capture_failed",
                "assessment_id": assessment.id,
                "scoring_failed": True,
            }
        return {"status": "already_submitted", "assessment_id": assessment.id}
    if current_status == AssessmentStatus.IN_PROGRESS:
        assessment.scoring_failed = True
        append_assessment_timeline_event(
            assessment,
            "auto_submit_timeout_capture_failed",
            {"error": str(getattr(exc, "detail", exc))[:500]},
        )
        db.commit()
        return {
            "status": "capture_failed",
            "assessment_id": assessment.id,
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
