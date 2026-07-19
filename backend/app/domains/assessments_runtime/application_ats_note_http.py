"""Small HTTP adapter for canonical recruiter ATS-note dispatch."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.user import User
from ...services.ats_job_run_errors import AtsJobRunPersistenceError
from ...services.ats_note_dispatch import (
    AtsNoteQueueError,
    enqueue_application_ats_note,
)


def queue_recruiter_workable_note(
    db: Session,
    *,
    application: CandidateApplication,
    current_user: User,
    body: str,
    request_key: str,
) -> dict[str, int | str]:
    """Translate canonical queue refusals into stable recruiter HTTP errors."""

    try:
        job_run_id = enqueue_application_ats_note(
            db,
            organization_id=int(current_user.organization_id),
            application_id=int(application.id),
            body=body,
            provider="workable",
            actor_type="recruiter",
            actor_id=int(current_user.id),
            request_key=request_key,
        )
    except AtsNoteQueueError as exc:
        if exc.code == "workable_disabled":
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "WORKABLE_INTEGRATION_DISABLED",
                    "message": exc.message,
                },
            ) from exc
        if exc.code == "idempotency_conflict":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ATS_NOTE_IDEMPOTENCY_CONFLICT",
                    "message": exc.message,
                },
            ) from exc
        if exc.code in {
            "workable_not_configured",
            "bullhorn_disabled",
            "bullhorn_not_configured",
        }:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ATS_NOTE_PROVIDER_UNAVAILABLE",
                    "message": exc.message,
                },
            ) from exc
        status_code = 400 if exc.code == "not_linked" else 422
        if exc.code.endswith("_unavailable"):
            status_code = 409
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    except AtsJobRunPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "ATS operation was not queued because durable tracking is "
                "temporarily unavailable. No provider update was sent; try again."
            ),
        ) from exc
    return {
        "status": "queued",
        "application_id": int(application.id),
        "job_run_id": int(job_run_id),
    }


__all__ = ["queue_recruiter_workable_note"]
