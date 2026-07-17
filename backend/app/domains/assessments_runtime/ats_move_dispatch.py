"""Durable provider dispatch for recruiter ATS stage moves."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
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

    application_id = int(app.id)
    from .related_role_actions import require_application_edit_action

    # The initialization commit released the original authorization locks.
    # Re-authorize under a fresh application/roster lock so a permission or
    # related-role revocation in that gap cannot enqueue provider work.
    app = require_application_edit_action(
        db,
        current_user=current_user,
        application_id=application_id,
        acting_role_id=data.acting_role_id,
    )
    owner_role = (
        db.query(Role)
        .filter(
            Role.id == int(app.role_id),
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    if owner_role is None:
        db.rollback()
        raise HTTPException(status_code=409, detail="The owning role is no longer available")
    acting_role = None
    related_evaluation = None
    if data.acting_role_id is not None:
        acting_role = (
            db.query(Role)
            .filter(
                Role.id == int(data.acting_role_id),
                Role.organization_id == int(current_user.organization_id),
                Role.deleted_at.is_(None),
            )
            .populate_existing()
            .with_for_update(of=Role)
            .one_or_none()
        )
        related_evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id
                == int(current_user.organization_id),
                SisterRoleEvaluation.role_id == int(data.acting_role_id),
                SisterRoleEvaluation.source_application_id == application_id,
            )
            .populate_existing()
            .with_for_update(of=SisterRoleEvaluation)
            .one_or_none()
        )
        if acting_role is None or related_evaluation is None:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="The related-role roster changed before the ATS move was queued",
            )
    from ...services.ats_stage_move_dispatch_snapshot import (
        build_stage_move_dispatch_payload,
    )

    try:
        authority_payload = build_stage_move_dispatch_payload(
            app=app,
            provider=provider_name,
            target_stage=target_stage,
            owner_role=owner_role,
            acting_role=acting_role,
            related_evaluation=related_evaluation,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    from ...services.ats_stage_move_dispatch_state import (
        StageMoveDispatchBlocked,
        plan_stage_move_dispatch,
    )

    try:
        dispatch = plan_stage_move_dispatch(
            db,
            app=app,
            organization_id=int(current_user.organization_id),
            operation_id=str(authority_payload["operation_id"]),
        )
    except StageMoveDispatchBlocked as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    # The queue publisher and eventual provider call must not inherit the
    # read transaction opened while copying the frozen authority snapshot.
    db.rollback()

    job_run_id = dispatch.job_run_id
    if dispatch.action == "enqueue":
        try:
            job_run_id = workable_op_runner.enqueue_workable_op(
                organization_id=current_user.organization_id,
                op_type=workable_op_runner.OP_MOVE_STAGE,
                payload={
                    **authority_payload,
                    "user_id": current_user.id,
                    "reason": data.reason,
                },
                dispatch_key=dispatch.dispatch_key,
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
    if dispatch.action != "confirmed":
        response.ats_writeback_status = "queued"
        response.ats_writeback_job_run_id = job_run_id
    return response


__all__ = ["queue_application_ats_move"]
