"""Preview and enqueue routes for recruiter-confirmed CV-gap rejection."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_current_user
from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..models.agent_needs_input import AgentNeedsInput
from ..models.user import User
from ..platform.database import get_db
from ..services.ats_job_run_errors import AtsJobRunPersistenceError
from ..services.cv_gap_rejection_authority import (
    CV_GAP_REJECTION_SPECS,
    CvGapAuthorityConflict,
    cv_gap_rejection_preview,
    lock_and_validate_cv_gap_authority,
)
from ..services.cv_gap_rejection_batch import initial_cv_gap_rejection_progress
from ..services.workable_op_runner import OP_REJECT_CV_GAP, enqueue_workable_op
from .cv_gap_rejection_contracts import (
    CvGapRejectPreview,
    RejectCvGapAccepted,
    RejectCvGapBody,
)


def _cv_gap_row_or_error(
    db: Session,
    *,
    needs_input_id: int,
    organization_id: int,
) -> AgentNeedsInput:
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == int(needs_input_id),
            AgentNeedsInput.organization_id == int(organization_id),
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="needs_input row not found")
    if row.kind not in CV_GAP_REJECTION_SPECS:
        raise HTTPException(
            status_code=422,
            detail="reject-cv-gap only applies to missing_cv / cv_unreadable items",
        )
    if not row.is_open:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CV_GAP_CARD_CHANGED",
                "message": "This CV-gap request is already resolved or dismissed.",
            },
        )
    return row


def get_reject_cv_gap_preview(
    needs_input_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CvGapRejectPreview:
    """Refresh the exact proof required by the asynchronous mutation."""

    organization_id = int(user.organization_id)
    row = _cv_gap_row_or_error(
        db,
        needs_input_id=needs_input_id,
        organization_id=organization_id,
    )
    require_job_permission(
        db,
        current_user=user,
        role_id=int(row.role_id),
        permission=JobPermission.CONTROL_AGENT,
        lock_for_update=False,
    )
    preview = cv_gap_rejection_preview(
        db,
        organization_id=organization_id,
        role_id=int(row.role_id),
        kind=str(row.kind),
    )
    if preview is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CV_GAP_ROLE_CHANGED",
                "message": "The job or its ATS owner is no longer available.",
            },
        )
    return CvGapRejectPreview.model_validate(preview)


def reject_cv_gap(
    needs_input_id: int,
    body: RejectCvGapBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RejectCvGapAccepted:
    """Validate an exact proof, then enqueue without inline provider I/O."""

    organization_id = int(user.organization_id)
    user_id = int(user.id)
    row = _cv_gap_row_or_error(
        db,
        needs_input_id=needs_input_id,
        organization_id=organization_id,
    )
    confirmed_role_id = int(row.role_id)
    confirmed_kind = str(row.kind)
    try:
        current, _ = lock_and_validate_cv_gap_authority(
            db,
            organization_id=organization_id,
            role_id=confirmed_role_id,
            kind=confirmed_kind,
            current_user=user,
            expected_owner_role_version=body.expected_owner_role_version,
            expected_role_family=body.expected_role_family,
            expected_application_ids=body.application_ids,
        )
    except CvGapAuthorityConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                **(
                    {"current_preview": exc.current_preview}
                    if exc.current_preview is not None
                    else {}
                ),
            },
        ) from exc

    locked_row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == int(needs_input_id),
            AgentNeedsInput.organization_id == organization_id,
        )
        .populate_existing()
        .with_for_update(of=AgentNeedsInput)
        .one_or_none()
    )
    if (
        locked_row is None
        or not locked_row.is_open
        or int(locked_row.role_id) != confirmed_role_id
        or str(locked_row.kind) != confirmed_kind
    ):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CV_GAP_CARD_CHANGED",
                "message": "The CV-gap request changed. Refresh before confirming again.",
            },
        )

    application_ids = [int(value) for value in body.application_ids]
    payload = {
        "needs_input_id": int(needs_input_id),
        "role_id": confirmed_role_id,
        "owner_role_id": int(current["owner_role_id"]),
        "kind": confirmed_kind,
        "application_ids": application_ids,
        "expected_owner_role_version": int(body.expected_owner_role_version),
        "expected_role_family": body.expected_role_family.model_dump(),
        "user_id": user_id,
    }
    progress = initial_cv_gap_rejection_progress(application_ids)
    db.commit()
    try:
        job_run_id = enqueue_workable_op(
            organization_id=organization_id,
            op_type=OP_REJECT_CV_GAP,
            payload=payload,
            scope_id=int(current["owner_role_id"]),
            counters={"op_type": OP_REJECT_CV_GAP, "progress": progress},
        )
    except AtsJobRunPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CV_GAP_JOB_RECEIPT_FAILED",
                "message": "The rejection batch was not queued. No ATS action was started.",
            },
        ) from exc

    return RejectCvGapAccepted(
        job_run_id=int(job_run_id),
        status="queued",
        accepted_count=len(application_ids),
        application_ids=application_ids,
    )


__all__ = ["get_reject_cv_gap_preview", "reject_cv_gap"]
