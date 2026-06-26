"""Public, no-auth resolver for shareable job pages.

Mirrors the top-reports public route: token in the path, optional-auth (so a
stray Authorization header never bounces an anonymous viewer), and the
public-safe snapshot returned in one round-trip. Mounted at app root under
``/api/v1/public`` (the URL the recruiter shares resolves in any browser).

Deliberately returns NO client / rate / margin — only what a candidate should
see. ``organization_name`` is the poster (the consultancy / employer).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.job_page import JOB_PAGE_STATUS_CLOSED, JobPage
from ...models.user import User
from ...platform.database import get_db

public_router = APIRouter(prefix="/api/v1/public", tags=["Job pages"])


@public_router.get("/job/{token}")
def view_job_page(
    token: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    page = db.query(JobPage).filter(JobPage.token == token).first()
    # 404 for both "no such page" and a closed page — a closed listing should
    # read as gone, not as "exists but unavailable".
    if page is None or page.status == JOB_PAGE_STATUS_CLOSED:
        raise HTTPException(status_code=404, detail="Job not found")

    org = page.organization
    return {
        "title": page.title,
        "jd_markdown": page.jd_markdown,
        "location": page.location,
        "workplace_type": page.workplace_type,
        "employment_type": page.employment_type,
        "seniority": page.seniority,
        "salary_min": page.salary_min,
        "salary_max": page.salary_max,
        "salary_currency": page.salary_currency,
        "status": page.status,
        "organization_name": org.name if org else None,
    }
