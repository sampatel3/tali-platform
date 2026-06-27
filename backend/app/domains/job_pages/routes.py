"""Public, no-auth resolver for shareable job pages + the per-org careers board.

Mirrors the top-reports public route: token in the path, optional-auth (so a
stray Authorization header never bounces an anonymous viewer), and the
public-safe snapshot returned in one round-trip. Mounted at app root under
``/api/v1/public`` (the URL the recruiter shares resolves in any browser).

Two surfaces:
- ``GET /job/{token}`` — a single published page.
- ``GET /careers/{slug}`` — the org's whole careers board (all its OPEN pages),
  resolved by ``Organization.slug``.

Both deliberately return NO client / rate / margin — only what a candidate
should see. ``organization_name`` is the poster (the consultancy / employer).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.job_page import JOB_PAGE_STATUS_CLOSED, JOB_PAGE_STATUS_OPEN, JobPage
from ...models.organization import Organization
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db

public_router = APIRouter(prefix="/api/v1/public", tags=["Job pages"])


def _job_page_url(token: str) -> str:
    """Public job-page URL. ``/job/{token}`` relative when FRONTEND_URL is empty."""
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/job/{token}" if base else f"/job/{token}"


def format_salary_band(
    salary_min: int | None,
    salary_max: int | None,
    currency: str | None,
) -> str:
    """Format a public-facing comp band, e.g. ``"AED 20,000–28,000 / year"``.

    Currency defaults to AED (UAE-based org). Returns ``""`` when there is no
    band at all (neither min nor max). A one-sided band renders the value it
    has ("AED 20,000+ / year" for a floor only, "up to AED 28,000 / year" for
    a ceiling only). Always per year (the only period the public page shows).
    """
    cur = (currency or "AED").strip() or "AED"
    if salary_min and salary_max:
        return f"{cur} {salary_min:,}–{salary_max:,} / year"
    if salary_min:
        return f"{cur} {salary_min:,}+ / year"
    if salary_max:
        return f"up to {cur} {salary_max:,} / year"
    return ""


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


@public_router.get("/careers/{slug}")
def view_careers_board(
    slug: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """The org's PUBLIC careers board: every OPEN job page it has published.

    Resolved by ``Organization.slug``. 404 when there is no org with that slug
    (an org without a slug is unreachable here by construction). An org with no
    open pages returns an empty ``jobs`` list — a valid, live-but-empty board.

    Each job carries only the public-safe snapshot (title / location / comp
    band / type) — NEVER any client / rate / margin. Newest first.
    """
    slug = (slug or "").strip()
    org = (
        db.query(Organization).filter(Organization.slug == slug).first()
        if slug
        else None
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Careers page not found")

    pages = (
        db.query(JobPage)
        .filter(
            JobPage.organization_id == org.id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
        )
        .order_by(JobPage.published_at.desc(), JobPage.id.desc())
        .all()
    )

    return {
        "organization_name": org.name,
        "slug": org.slug,
        "jobs": [
            {
                "token": page.token,
                "url": _job_page_url(page.token),
                "title": page.title,
                "location": page.location,
                "workplace_type": page.workplace_type,
                "employment_type": page.employment_type,
                "seniority": page.seniority,
                "salary": format_salary_band(
                    page.salary_min, page.salary_max, page.salary_currency
                ),
                "published_at": page.published_at.isoformat()
                if page.published_at
                else None,
            }
            for page in pages
        ],
    }
