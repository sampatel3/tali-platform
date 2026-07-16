"""Authorization and mutations for a related role's local candidate funnel."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...models.user import User
from ...services.sister_role_service import (
    source_application_is_globally_closed,
    transition_related_role_stage,
)
from .job_authorization import JobPermission, require_job_permission


def require_related_role_application_action(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
) -> Role:
    """Authorize one action against a related role's shared roster."""

    role = db.get(Role, int(related_role_id))
    if (
        role is None
        or int(role.organization_id) != int(current_user.organization_id)
        or str(role.role_kind or "") != ROLE_KIND_SISTER
        or int(role.ats_owner_role_id or 0) != int(application.role_id)
    ):
        raise HTTPException(
            status_code=409,
            detail="Related role does not own this shared candidate roster",
        )
    require_job_permission(
        db,
        current_user=current_user,
        role_id=int(role.id),
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=False,
    )
    if source_application_is_globally_closed(application):
        raise HTTPException(
            status_code=409,
            detail="A disqualified or closed shared ATS application cannot be changed",
        )
    return role


def move_related_role_application_stage(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
    to_stage: str,
) -> tuple[Role, SisterRoleEvaluation]:
    """Move one candidate without changing another related role's stage."""

    role = require_related_role_application_action(
        db,
        current_user=current_user,
        related_role_id=related_role_id,
        application=application,
    )
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.source_application_id == int(application.id),
        )
        .with_for_update()
        .one_or_none()
    )
    if evaluation is None:
        raise HTTPException(
            status_code=404, detail="Related-role candidate state not found"
        )
    try:
        transition_related_role_stage(
            evaluation, to_stage=to_stage, source="recruiter"
        )
        db.commit()
        db.refresh(evaluation)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return role, evaluation


__all__ = [
    "move_related_role_application_stage",
    "require_related_role_application_action",
]
