"""Authorization and mutations for a related role's local candidate funnel."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...models.user import User
from ...services.related_role_action_service import (
    lock_related_role_membership,
    transition_related_role_stage_action,
)
from ...services.sister_role_service import (
    related_role_action_restrictions,
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
    ):
        raise HTTPException(
            status_code=409,
            detail="The requested acting role is not a related role",
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

    if acting_role_id is None:
        application = get_application(
            int(application_id), int(current_user.organization_id), db
        )
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(application.role_id),
            permission=JobPermission.EDIT_ROLE,
        )
        return application

    # Explicit related-role membership survives source-row soft deletion. The
    # generic application reader intentionally hides deleted rows, so resolve
    # the evidence row here and let the membership check below be authoritative.
    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(current_user.organization_id),
        )
        .one_or_none()
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    role = _require_related_role_permission(
        db,
        current_user=current_user,
        related_role_id=int(acting_role_id),
        application=application,
        lock_for_update=True,
    )
    locked = lock_related_role_membership(
        db,
        application=application,
        acting_role_id=int(role.id),
        for_update=False,
    )
    assert locked is not None
    if not allow_closed_related:
        local_outcome = str(
            locked[1].application_outcome or "open"
        ).strip().lower()
        local_stage = str(locked[1].pipeline_stage or "applied").strip().lower()
        if local_outcome != "open" or local_stage == "advanced":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "related_role_application_resolved",
                    "message": "This candidate has already left this role's active flow.",
                    "role_id": int(role.id),
                    "pipeline_stage": local_stage,
                    "application_outcome": local_outcome,
                },
            )
        restrictions = related_role_action_restrictions(
            role=role,
            evaluation=locked[1],
            source_application=application,
        )
        if not bool(restrictions.get("can_advance_in_ats")):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "related_role_ats_write_restricted",
                    "message": "This role remains active, but its shared ATS link cannot accept that write.",
                    "restriction_codes": restrictions.get("codes") or [],
                },
            )
    return application


def require_related_role_application_action(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
) -> Role:
    """Authorize one action against a related role's independent roster."""

    role = _require_related_role_permission(
        db,
        current_user=current_user,
        related_role_id=int(related_role_id),
        application=application,
        lock_for_update=False,
    )
    lock_related_role_membership(
        db,
        application=application,
        acting_role_id=int(role.id),
        for_update=False,
    )
    return role


def move_related_role_application_stage(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
    to_stage: str,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[Role, SisterRoleEvaluation]:
    """Move one candidate in the acting related role's local lifecycle."""

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
    try:
        result = transition_related_role_stage_action(
            db,
            application=application,
            acting_role_id=int(role.id),
            to_stage=to_stage,
            source="recruiter",
            actor_type="recruiter",
            actor_id=int(current_user.id),
            reason=f"Stage updated in related role {role.name}",
            metadata={"acting_role_id": int(role.id)},
            idempotency_key=idempotency_key,
            expected_version=expected_version,
        )
        assert result is not None
        evaluation = result.evaluation
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
