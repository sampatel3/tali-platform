"""Canonical application-first authorization for candidate mutations."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.user import User
from .job_authorization import JobPermission, require_job_permission
from .role_support import get_application


def lock_application_for_mutation(
    db: Session,
    *,
    application_id: int,
    organization_id: int,
    missing_status_code: int = 404,
    missing_detail: str = "Application not found",
) -> CandidateApplication:
    """Reload and lock one live application before any canonical Role lock.

    ``populate_existing`` is deliberate: a transaction may already hold an
    older instance in its identity map while it waits for another writer. The
    row state observed after ``FOR UPDATE`` wins over that stale snapshot.
    """

    application = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.organization),
            joinedload(CandidateApplication.role),
            joinedload(CandidateApplication.interviews),
            joinedload(CandidateApplication.assessments).joinedload(Assessment.task),
        )
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.deleted_at.is_(None),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if application is None:
        raise HTTPException(status_code=missing_status_code, detail=missing_detail)
    return application


def require_application_job_permission(
    db: Session,
    *,
    current_user: User,
    application_id: int,
    permission: JobPermission,
    lock_for_update: bool = True,
) -> CandidateApplication:
    """Authorize an application mutation in application -> role lock order."""

    if lock_for_update:
        application = lock_application_for_mutation(
            db,
            application_id=application_id,
            organization_id=int(current_user.organization_id),
        )
    else:
        application = get_application(
            int(application_id), int(current_user.organization_id), db
        )
    require_job_permission(
        db,
        current_user=current_user,
        role_id=int(application.role_id),
        permission=permission,
        lock_for_update=lock_for_update,
    )
    return application


__all__ = ["lock_application_for_mutation", "require_application_job_permission"]
