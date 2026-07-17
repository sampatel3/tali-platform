"""Shared-application helpers for assessment create/retake routes."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.user import User
from ...services.related_role_application_runtime import related_role_for_application
from .job_authorization import JobPermission, require_job_permission
from .role_support import latest_valid_role_assessment


def latest_active_assessment_for_role(
    app: CandidateApplication,
    db: Session,
    *,
    role_id: int | None = None,
) -> Assessment | None:
    return latest_valid_role_assessment(
        candidate_id=app.candidate_id,
        role_id=int(role_id or app.role_id),
        org_id=app.organization_id,
        db=db,
    )


def assessment_role_for_application(
    db: Session,
    *,
    app: CandidateApplication,
    role_id: int,
    current_user: User,
) -> Role:
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(role_id),
        permission=JobPermission.CONTROL_AGENT,
    )
    if int(role.id) == int(app.role_id):
        return role
    if related_role_for_application(
        db, role_id=int(role.id), application=app
    ) is None:
        raise HTTPException(
            status_code=422,
            detail="Application is not in this role's shared candidate pool",
        )
    return role


__all__ = ["assessment_role_for_application", "latest_active_assessment_for_role"]
