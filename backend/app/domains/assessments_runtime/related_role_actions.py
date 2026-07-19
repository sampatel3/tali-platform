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
    source_application_is_globally_advanced,
    source_application_is_globally_closed,
    transition_related_role_stage,
)
from .application_mutation_authorization import (
    lock_application_for_mutation,
)
from .job_authorization import JobPermission, require_job_permission


def _require_related_role_permission(
    db: Session,
    *,
    current_user: User,
    related_role_id: int,
    application: CandidateApplication,
    lock_for_update: bool,
) -> Role:
    # Backward-compatible private seam: route through the canonical
    # application-first authority path so a future caller cannot revive the
    # old related-role-before-owner lock order or skip roster membership.
    locked_application = lock_application_for_mutation(
        db,
        application_id=int(application.id),
        organization_id=int(current_user.organization_id),
    )
    return _require_related_role_for_locked_application(
        db,
        current_user=current_user,
        related_role_id=int(related_role_id),
        locked_application=locked_application,
        lock_for_update=lock_for_update,
    )


def require_application_action_permission(
    db: Session,
    *,
    current_user: User,
    application_id: int,
    acting_role_id: int | None,
    allow_closed_related: bool = False,
) -> CandidateApplication:
    """Authorize a shared-application action against its visible role."""

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
        allow_already_rejected=allow_closed_related,
        allow_globally_advanced=allow_closed_related,
    )
    return application


def require_application_edit_action(
    db: Session,
    *,
    current_user: User,
    application_id: int,
    acting_role_id: int | None,
    lock_role_for_update: bool = True,
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
        lock_for_update=lock_role_for_update,
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
    reopens_rejected_application = (
        current_outcome == "rejected" and normalized_target == "open"
    )
    allow_already_rejected = (
        current_outcome == normalized_target == "rejected"
    ) or reopens_rejected_application
    # Authorize before loading the role family so callers cannot use a reject
    # request to discover family membership for an application they cannot
    # edit. Re-check after the family rows are locked to close the race with a
    # concurrent hiring-team change.
    _authorize_locked_application_edit(
        db,
        current_user=current_user,
        acting_role_id=acting_role_id,
        locked_application=application,
        allow_already_rejected=allow_already_rejected,
        lock_for_update=False,
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
        allow_already_rejected=allow_already_rejected,
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
    allow_globally_advanced: bool = False,
    lock_for_update: bool = True,
) -> None:
    if acting_role_id is None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(locked_application.role_id),
            permission=JobPermission.EDIT_ROLE,
            lock_for_update=lock_for_update,
        )
        return
    _require_related_role_for_locked_application(
        db,
        current_user=current_user,
        related_role_id=int(acting_role_id),
        locked_application=locked_application,
        allow_already_rejected=allow_already_rejected,
        allow_globally_advanced=allow_globally_advanced,
        lock_for_update=lock_for_update,
    )


def authorize_locked_application_edit(
    db: Session,
    *,
    current_user: User,
    acting_role_id: int | None,
    locked_application: CandidateApplication,
    allow_already_rejected: bool = False,
    allow_globally_advanced: bool = False,
    lock_for_update: bool = True,
) -> None:
    """Public lock-preserving authorization seam for shared roster actions."""

    _authorize_locked_application_edit(
        db,
        current_user=current_user,
        acting_role_id=acting_role_id,
        locked_application=locked_application,
        allow_already_rejected=allow_already_rejected,
        allow_globally_advanced=allow_globally_advanced,
        lock_for_update=lock_for_update,
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
    allow_globally_advanced: bool = False,
    lock_for_update: bool = True,
) -> Role:
    """Apply related-roster policy after the application row is locked."""

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(related_role_id),
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=lock_for_update,
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
    if (
        source_application_is_globally_advanced(locked_application)
        and not allow_globally_advanced
    ):
        raise HTTPException(
            status_code=409,
            detail="An advanced shared ATS application cannot be moved or reopened",
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
    "authorize_locked_application_edit",
    "move_related_role_application_stage",
    "require_application_edit_action",
    "require_application_outcome_action",
    "require_application_action_permission",
    "require_related_role_application_action",
]
