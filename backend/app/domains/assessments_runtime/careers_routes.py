"""P1: public careers site (read-only, no auth).

Lists an org's published roles and serves each posting with schema.org JobPosting
JSON-LD (Google for Jobs). Read-only + low-risk; the public APPLY (write) endpoint
ships separately behind the Redis anti-abuse gate. Registered WITHOUT the
/api/v1 prefix (public surface).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...platform.database import get_db
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
