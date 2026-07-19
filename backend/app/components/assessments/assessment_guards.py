"""Neutral candidate-assessment lifecycle guards."""

from __future__ import annotations

from fastapi import HTTPException

from ...models.assessment import Assessment, AssessmentStatus

CV_UPLOAD_STATUSES = frozenset(
    {AssessmentStatus.PENDING, AssessmentStatus.IN_PROGRESS}
)


def enforce_not_paused(assessment: Assessment) -> None:
    if getattr(assessment, "is_timer_paused", False):
        raise HTTPException(
            status_code=423,
            detail={
                "code": "ASSESSMENT_PAUSED",
                "message": "Assessment is paused while AI assistant is unavailable",
                "pause_reason": getattr(assessment, "pause_reason", None),
            },
        )


def enforce_cv_uploadable(assessment: Assessment) -> None:
    """Allow pre-start/in-progress CV metadata only on live, non-void rows."""

    if bool(getattr(assessment, "is_voided", False)):
        raise HTTPException(status_code=400, detail="assessment_voided")
    if assessment.status not in CV_UPLOAD_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Assessment is no longer open for CV uploads",
        )
    enforce_not_paused(assessment)


__all__ = ["CV_UPLOAD_STATUSES", "enforce_cv_uploadable", "enforce_not_paused"]
