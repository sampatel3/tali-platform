from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...schemas.role import ApplicationResponse, RoleResponse


def role_has_job_spec(role: Role) -> bool:
    return bool((role.job_spec_file_url or "").strip() or (role.job_spec_text or "").strip())


def get_role(role_id: int, org_id: int, db: Session) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def get_application(application_id: int, org_id: int, db: Session) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate), joinedload(CandidateApplication.role))
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def role_to_response(role: Role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        name=role.name,
        description=role.description,
        source=role.source,
        workable_job_id=role.workable_job_id,
        job_spec_filename=role.job_spec_filename,
        job_spec_uploaded_at=role.job_spec_uploaded_at,
        job_spec_present=role_has_job_spec(role),
        interview_focus=role.interview_focus,
        interview_focus_generated_at=role.interview_focus_generated_at,
        tasks_count=len(role.tasks or []),
        applications_count=len([a for a in (role.applications or []) if getattr(a, "deleted_at", None) is None]),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def application_to_response(app: CandidateApplication) -> ApplicationResponse:
    candidate = app.candidate
    return ApplicationResponse(
        id=app.id,
        organization_id=app.organization_id,
        candidate_id=app.candidate_id,
        role_id=app.role_id,
        status=app.status,
        notes=app.notes,
        candidate_email=(candidate.email if candidate else ""),
        candidate_name=(candidate.full_name if candidate else None),
        candidate_position=(candidate.position if candidate else None),
        cv_filename=app.cv_filename,
        cv_uploaded_at=app.cv_uploaded_at,
        cv_match_score=app.cv_match_score,
        cv_match_details=app.cv_match_details,
        cv_match_scored_at=app.cv_match_scored_at,
        source=app.source,
        workable_candidate_id=app.workable_candidate_id,
        workable_stage=app.workable_stage,
        workable_score_raw=app.workable_score_raw,
        workable_score=app.workable_score,
        workable_score_source=app.workable_score_source,
        rank_score=app.rank_score,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )
