from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...schemas.role import ApplicationResponse, RoleResponse


def _normalize_cv_match_score_for_response(score: float | None, details: dict | None) -> float | None:
    if score is None:
        return None
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    scale = str((details or {}).get("score_scale") or "").strip().lower()
    if "100" in scale:
        normalized = numeric
    elif "10" in scale and "100" not in scale:
        normalized = numeric * 10.0
    elif numeric <= 10.0:
        normalized = numeric * 10.0
    else:
        normalized = numeric
    return round(max(0.0, min(100.0, normalized)), 1)


def role_has_job_spec(role: Role) -> bool:
    return bool(
        (role.job_spec_file_url or "").strip()
        or (role.job_spec_text or "").strip()
        or (role.description or "").strip()
    )


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


def role_to_response(
    role: Role,
    *,
    tasks_count: int | None = None,
    applications_count: int | None = None,
) -> RoleResponse:
    if tasks_count is None:
        tasks_count = len(role.tasks or [])
    if applications_count is None:
        applications_count = len(
            [a for a in (role.applications or []) if getattr(a, "deleted_at", None) is None]
        )

    return RoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        name=role.name,
        description=role.description,
        additional_requirements=role.additional_requirements,
        source=role.source,
        workable_job_id=role.workable_job_id,
        job_spec_filename=role.job_spec_filename,
        job_spec_text=role.job_spec_text,
        job_spec_uploaded_at=role.job_spec_uploaded_at,
        job_spec_present=role_has_job_spec(role),
        interview_focus=role.interview_focus,
        interview_focus_generated_at=role.interview_focus_generated_at,
        tasks_count=tasks_count,
        applications_count=applications_count,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _candidate_location(candidate) -> str | None:
    if not candidate:
        return None
    city = (candidate.location_city or "").strip()
    country = (candidate.location_country or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country or None


def application_to_response(app: CandidateApplication) -> ApplicationResponse:
    candidate = app.candidate
    raw_details = app.cv_match_details if isinstance(app.cv_match_details, dict) else {}
    cv_match_score = _normalize_cv_match_score_for_response(app.cv_match_score, raw_details)
    cv_match_details = dict(raw_details)
    if cv_match_score is not None and "score_scale" not in cv_match_details:
        cv_match_details["score_scale"] = "0-100"

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
        cv_match_score=cv_match_score,
        cv_match_details=cv_match_details or None,
        cv_match_scored_at=app.cv_match_scored_at,
        source=app.source,
        workable_candidate_id=app.workable_candidate_id,
        workable_stage=app.workable_stage,
        workable_score_raw=app.workable_score_raw,
        workable_score=app.workable_score,
        workable_score_source=app.workable_score_source,
        rank_score=app.rank_score,
        candidate_headline=(candidate.headline if candidate else None),
        candidate_image_url=(candidate.image_url if candidate else None),
        candidate_location=_candidate_location(candidate),
        candidate_phone=(candidate.phone if candidate else None),
        candidate_profile_url=(candidate.profile_url if candidate else None),
        candidate_social_profiles=(candidate.social_profiles if candidate else None),
        candidate_tags=(candidate.tags if candidate else None),
        candidate_skills=(candidate.skills if candidate else None),
        candidate_education=(candidate.education_entries if candidate else None),
        candidate_experience=(candidate.experience_entries if candidate else None),
        candidate_summary=(candidate.summary if candidate else None),
        candidate_workable_created_at=(candidate.workable_created_at if candidate else None),
        workable_sourced=app.workable_sourced,
        workable_profile_url=app.workable_profile_url,
        workable_enriched=(candidate.workable_enriched if candidate else None),
        created_at=app.created_at,
        updated_at=app.updated_at,
    )
