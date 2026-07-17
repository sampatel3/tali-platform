"""Authorization and mutations for a related role's local candidate funnel."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.role import ROLE_KIND_SISTER, Role
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...models.user import User
from ...schemas.role import RoleFamilyResponse
from ...services import related_role_pipeline_queries as related_pipeline
from ...services.role_family_reject_authority import (
    lock_current_role_families,
    require_expected_role_family,
)
from ...services.sister_role_service import (
    source_application_is_globally_closed,
    transition_related_role_stage,
)
from .application_mutation_authorization import (
    lock_application_for_mutation,
)
from .job_authorization import JobPermission, require_job_permission


def require_application_edit_action(
    db: Session,
    *,
    current_user: User,
    application_id: int,
    acting_role_id: int | None,
) -> CandidateApplication:
    """Authorize an application edit through its owner or a live related roster."""

    application = lock_application_for_mutation(
        db,
        application_id=int(application_id),
        organization_id=int(current_user.organization_id),
    )
    _authorize_locked_application_edit(
        db,
        current_user=current_user,
        acting_role_id=acting_role_id,
        locked_application=application,
    )
    return application


def require_application_outcome_action(
    db: Session,
    *,
    current_user: User,
    application_id: int,
    acting_role_id: int | None,
    target_outcome: str,
    expected_role_family: RoleFamilyResponse | None,
) -> CandidateApplication:
    """Authorize an outcome edit and bind a fresh reject to its shown family."""

    application = lock_application_for_mutation(
        db,
        application_id=int(application_id),
        organization_id=int(current_user.organization_id),
    )
    current_outcome = str(application.application_outcome or "open").strip().lower()
    normalized_target = str(target_outcome or current_outcome).strip().lower()
    rejects_open_application = (
        current_outcome == "open" and normalized_target == "rejected"
    )
    authority_role_id = int(
        application.role_id if acting_role_id is None else acting_role_id
    )
    current_families = (
        lock_current_role_families(
            db,
            organization_id=int(current_user.organization_id),
            role_ids=[authority_role_id],
        )
        if rejects_open_application
        else {}
    )
    _authorize_locked_application_edit(
        db,
        current_user=current_user,
        acting_role_id=acting_role_id,
        locked_application=application,
        allow_already_rejected=(
            current_outcome == normalized_target == "rejected"
        ),
    )
    current_family = current_families.get(authority_role_id)
    if current_family is not None:
        require_expected_role_family(
            expected=expected_role_family,
            current=current_family,
        )
    return application


def _authorize_locked_application_edit(
    db: Session,
    *,
    current_user: User,
    acting_role_id: int | None,
    locked_application: CandidateApplication,
    allow_already_rejected: bool = False,
) -> None:
    if acting_role_id is None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(locked_application.role_id),
            permission=JobPermission.EDIT_ROLE,
        )
        return
    _require_related_role_for_locked_application(
        db,
        current_user=current_user,
        related_role_id=int(acting_role_id),
        locked_application=locked_application,
        allow_already_rejected=allow_already_rejected,
    )


def authorize_locked_application_edit(
    db: Session,
    *,
    current_user: User,
    acting_role_id: int | None,
    locked_application: CandidateApplication,
    allow_already_rejected: bool = False,
) -> None:
    """Public lock-preserving authorization seam for shared roster actions."""

    _authorize_locked_application_edit(
        db,
        current_user=current_user,
        acting_role_id=acting_role_id,
        locked_application=locked_application,
        allow_already_rejected=allow_already_rejected,
    )


def require_related_role_application_action(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
) -> Role:
    """Authorize one action against a related role's shared roster."""

    # The durable ATS worker locks application -> related role -> evaluation.
    # Take the same leading lock here so hiring-team changes still serialize
    # through the canonical role lock without creating an inverse-order
    # deadlock with an in-flight provider operation.
    locked_application = lock_application_for_mutation(
        db,
        application_id=int(application.id),
        organization_id=int(current_user.organization_id),
        missing_status_code=409,
        missing_detail="Related role does not own this shared candidate roster",
    )
    return _require_related_role_for_locked_application(
        db,
        current_user=current_user,
        related_role_id=related_role_id,
        locked_application=locked_application,
    )


def _require_related_role_for_locked_application(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    locked_application: CandidateApplication,
    allow_already_rejected: bool = False,
) -> Role:
    """Apply related-roster policy after the application row is locked."""

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(related_role_id),
        permission=JobPermission.EDIT_ROLE,
    )
    if (
        int(role.organization_id) != int(current_user.organization_id)
        or str(role.role_kind or "") != ROLE_KIND_SISTER
        or int(role.ats_owner_role_id or 0) != int(locked_application.role_id)
    ):
        raise HTTPException(
            status_code=409,
            detail="Related role does not own this shared candidate roster",
        )
    if (
        source_application_is_globally_closed(locked_application)
        and not allow_already_rejected
    ):
        raise HTTPException(
            status_code=409,
            detail="A disqualified or closed shared ATS application cannot be changed",
        )
    roster_evaluation = (
        db.query(SisterRoleEvaluation.id)
        .join(
            CandidateApplication,
            CandidateApplication.id
            == SisterRoleEvaluation.source_application_id,
        )
        .filter(
            SisterRoleEvaluation.organization_id
            == int(current_user.organization_id),
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.source_application_id
            == int(locked_application.id),
            related_pipeline.valid_source_scope(
                organization_id=int(current_user.organization_id),
                owner_role_id=int(role.ats_owner_role_id),
            ),
        )
        .one_or_none()
    )
    if roster_evaluation is None:
        # A related role shares its owner's ATS application table, but it does
        # not gain mutation authority over every source candidate. Only a row
        # projected into this role's own roster may be changed from that role.
        raise HTTPException(
            status_code=409,
            detail="Related role does not own this shared candidate roster",
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
    "authorize_locked_application_edit",
    "move_related_role_application_stage",
    "require_application_edit_action",
    "require_application_outcome_action",
    "require_related_role_application_action",
]
