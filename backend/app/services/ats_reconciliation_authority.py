"""Tenant and shared-roster authorization for ATS reconciliation."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..models.candidate_application import CandidateApplication
from ..models.user import User


def lock_reconciliation_application(
    db: Session,
    *,
    application_id: int,
    current_user: User,
    acting_role_id: int | None,
) -> CandidateApplication:
    """Lock the tenant row and authorize its owner or exact related roster."""

    organization_id = getattr(current_user, "organization_id", None)
    if organization_id is None or not bool(getattr(current_user, "is_active", False)):
        raise HTTPException(status_code=403, detail="Forbidden")
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if acting_role_id is None:
        require_job_permission(
            db,
            current_user=current_user,
            role_id=int(app.role_id),
            permission=JobPermission.EDIT_ROLE,
        )
    else:
        # Related-role drawers show an owner's shared ATS row. The established
        # helper proves related kind, owner linkage, roster membership, tenant,
        # and hiring-team authority under the same application-first lock order.
        from ..domains.assessments_runtime.related_role_actions import (
            authorize_locked_application_edit,
        )

        authorize_locked_application_edit(
            db,
            current_user=current_user,
            acting_role_id=int(acting_role_id),
            locked_application=app,
            allow_already_rejected=True,
        )
    return app


__all__ = ["lock_reconciliation_application"]
