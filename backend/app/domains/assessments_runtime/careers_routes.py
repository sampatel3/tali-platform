"""P1: public careers site (read-only, no auth).

Lists an org's published roles and serves each posting with schema.org JobPosting
JSON-LD (Google for Jobs). Read-only + low-risk; the public APPLY (write) endpoint
ships separately behind the Redis anti-abuse gate. Registered WITHOUT the
/api/v1 prefix (public surface).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...platform.config import settings
from ...platform.database import get_db
from ...services.rate_limit import check_rate_limit
from .apply_service import submit_application
from .careers_service import (
    build_job_posting_jsonld,
    get_published_role,
    list_published_roles,
)

router = APIRouter(tags=["Careers (public)"])


class PublicRoleSummary(BaseModel):
    slug: str
    title: str
    department: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    location_city: str | None = None
    location_country: str | None = None


class PublicCareersResponse(BaseModel):
    organization: str
    jobs: list[PublicRoleSummary]


class PublicRoleDetail(PublicRoleSummary):
    description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    job_posting_jsonld: dict


def _summary(role) -> PublicRoleSummary:
    return PublicRoleSummary(
        slug=role.slug,
        title=role.name,
        department=role.department,
        employment_type=role.employment_type,
        workplace_type=role.workplace_type,
        location_city=role.location_city,
        location_country=role.location_country,
    )


@router.get("/careers/v1/{org_slug}/jobs", response_model=PublicCareersResponse)
def public_list_jobs(org_slug: str, db: Session = Depends(get_db)):
    org, roles = list_published_roles(db, org_slug)
    if org is None:
        raise HTTPException(status_code=404, detail="Unknown organization")
    return PublicCareersResponse(
        organization=org.name, jobs=[_summary(r) for r in roles]
    )


@router.get(
    "/careers/v1/{org_slug}/jobs/{role_slug}", response_model=PublicRoleDetail
)
def public_get_job(org_slug: str, role_slug: str, db: Session = Depends(get_db)):
    org, role = get_published_role(db, org_slug, role_slug)
    if role is None:
        raise HTTPException(status_code=404, detail="Job not found")
    summary = _summary(role)
    return PublicRoleDetail(
        **summary.model_dump(),
        description=(role.description or role.job_spec_text),
        salary_min=role.salary_min,
        salary_max=role.salary_max,
        salary_currency=role.salary_currency,
        salary_period=role.salary_period,
        job_posting_jsonld=build_job_posting_jsonld(role, org),
    )


class ApplyRequest(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    answers: dict = {}
    source_name: str | None = None
    resume_url: str | None = None


class ApplyResponse(BaseModel):
    application_id: int
    created: bool
    knockout_passed: bool
    failed_question_ids: list[int] = []


@router.post(
    "/careers/v1/{org_slug}/jobs/{role_slug}/apply",
    response_model=ApplyResponse,
    status_code=201,
)
def public_apply(
    org_slug: str,
    role_slug: str,
    payload: ApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Public apply (no auth). Flag-gated (503 when off) and rate-limited per
    client per role. Requires at least one contact key (email or phone)."""
    if not settings.ATS_PUBLIC_APPLY_ENABLED:
        raise HTTPException(status_code=503, detail="Applications are not open")
    if not (payload.email or payload.phone):
        raise HTTPException(status_code=422, detail="An email or phone is required")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(
        f"apply:{org_slug}:{role_slug}:{client_ip}",
        limit=settings.ATS_APPLY_RATE_LIMIT_PER_HOUR,
        window_seconds=3600,
    ):
        raise HTTPException(status_code=429, detail="Too many applications; try again later")

    org, role = get_published_role(db, org_slug, role_slug)
    if role is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = submit_application(
        db, org.id, role,
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        answers=payload.answers,
        source_name=payload.source_name,
        resume_url=payload.resume_url,
    )
    db.commit()
    return ApplyResponse(
        application_id=result.application.id,
        created=result.created,
        knockout_passed=result.knockout_passed,
        failed_question_ids=result.failed_question_ids,
    )
