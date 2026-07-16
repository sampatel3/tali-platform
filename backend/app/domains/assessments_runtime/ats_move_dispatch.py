"""Durable provider dispatch for recruiter ATS stage moves."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.user import User
from ...schemas.role import ApplicationResponse, WorkableMoveStageRequest
from .pipeline_service import ensure_pipeline_fields, initialize_pipeline_event_if_missing
from .role_support import application_to_response


def queue_application_ats_move(
    *,
    app: CandidateApplication,
    data: WorkableMoveStageRequest,
    db: Session,
    current_user: User,
    provider_name: str,
) -> ApplicationResponse:
    """Initialize local state and durably queue one provider-routed move."""

    target_stage = str(data.target_stage or "").strip()
    if (
        str(app.application_outcome or "open").strip().lower() != "open"
        or bool(app.workable_disqualified)
    ):
        raise HTTPException(
            status_code=409,
            detail="A closed or disqualified application cannot be moved in the ATS",
        )
    try:
        ensure_pipeline_fields(app)
        initialize_pipeline_event_if_missing(
            db,
            app=app,
            actor_type="system",
            actor_id=current_user.id,
            reason=f"Pipeline initialized before {provider_name.title()} hand-back",
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to initialize pipeline for hand-back"
        ) from exc

    # Resolve through the historical module boundary so callers/tests that
    # instrument the durable publisher continue to observe this dispatch.
    from ...services import workable_op_runner

    try:
        job_run_id = workable_op_runner.enqueue_workable_op(
            organization_id=current_user.organization_id,
            op_type=workable_op_runner.OP_MOVE_STAGE,
            payload={
                "application_id": int(app.id),
                "user_id": current_user.id,
                "target_stage": target_stage,
                "target_intent": target_stage if provider_name == "bullhorn" else None,
                "reason": data.reason,
                "acting_role_id": data.acting_role_id,
            },
        )
    except workable_op_runner.AtsJobRunPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "ATS operation was not queued because durable tracking is "
                "temporarily unavailable. No provider update was sent; try again."
            ),
        ) from exc
    db.refresh(app)
    response = application_to_response(app, use_cached_score_summary=True)
    response.ats_writeback_status = "queued"
    response.ats_writeback_job_run_id = job_run_id
    return response


__all__ = ["queue_application_ats_move"]
