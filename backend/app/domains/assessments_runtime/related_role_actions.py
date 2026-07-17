"""Authorization and mutations for a related role's local candidate funnel."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...models.user import User
from ...services.sister_role_service import (
    source_application_is_globally_advanced,
    source_application_is_globally_closed,
    transition_related_role_stage,
)
from .job_authorization import JobPermission, require_job_permission
from .role_support import get_application


def _require_related_role_permission(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
    lock_for_update: bool,
) -> Role:
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(related_role_id),
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=lock_for_update,
    )
    if (
        str(role.role_kind or "") != ROLE_KIND_SISTER
        or int(role.ats_owner_role_id or 0) != int(application.role_id)
    ):
        raise HTTPException(
            status_code=409,
            detail="Related role does not own this shared candidate roster",
        )
    return role


def require_application_action_permission(
    db: Session,
    *,
    current_user: User,
    application_id: int,
    acting_role_id: int | None,
    allow_closed_related: bool = False,
) -> CandidateApplication:
    """Authorize a shared-application action against its visible role."""

    application = get_application(
        int(application_id), int(current_user.organization_id), db
    )
    if acting_role_id is None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(application.role_id),
            permission=JobPermission.EDIT_ROLE,
        )
        return application

    _require_related_role_permission(
        db,
        current_user=current_user,
        related_role_id=int(acting_role_id),
        application=application,
        lock_for_update=True,
    )
    if not allow_closed_related and source_application_is_globally_closed(application):
        raise HTTPException(
            status_code=409,
            detail="A disqualified or closed shared ATS application cannot be changed",
        )
    if not allow_closed_related and source_application_is_globally_advanced(application):
        raise HTTPException(
            status_code=409,
            detail="An advanced shared ATS application cannot be moved or reopened",
        )
    return application


def require_related_role_application_action(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
) -> Role:
    """Authorize one action against a related role's shared roster."""

    role = _require_related_role_permission(
        db,
        current_user=current_user,
        related_role_id=int(related_role_id),
        application=application,
        lock_for_update=False,
    )
    if source_application_is_globally_closed(application):
        raise HTTPException(
            status_code=409,
            detail="A disqualified or closed shared ATS application cannot be changed",
        )
    if source_application_is_globally_advanced(application):
        raise HTTPException(
            status_code=409,
            detail="An advanced shared ATS application cannot be moved or reopened",
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
    """Move locally, except shared advance which hands off the whole family."""

    role = require_related_role_application_action(
        db,
        current_user=current_user,
        related_role_id=related_role_id,
        application=application,
    )
    application_query = db.query(CandidateApplication).filter(
        CandidateApplication.id == int(application.id),
        CandidateApplication.organization_id == int(application.organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        application_query = application_query.with_for_update()
    application = application_query.populate_existing().one()
    # Recheck after acquiring the shared row: another linked role may have
    # advanced/rejected while authorization was being evaluated.
    if source_application_is_globally_closed(application):
        raise HTTPException(
            status_code=409,
            detail="A disqualified or closed shared ATS application cannot be changed",
        )
    if source_application_is_globally_advanced(application):
        raise HTTPException(
            status_code=409,
            detail="An advanced shared ATS application cannot be moved or reopened",
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
        if str(to_stage or "").strip().lower() == "advanced":
            from ...actions import advance_stage
            from ...actions.types import Actor

            advance_stage.run(
                db,
                Actor.recruiter(current_user),
                organization_id=int(application.organization_id),
                application_id=int(application.id),
                to_stage="advanced",
                reason=f"Advanced from related role {role.name}",
                idempotency_key=(
                    f"related_role_advance:{role.id}:{application.id}"
                ),
                metadata={"acting_role_id": int(role.id)},
            )
        else:
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
    "require_application_action_permission",
    "require_related_role_application_action",
]
