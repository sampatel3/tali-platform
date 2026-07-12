"""Role distribution API — copy-paste artefacts + a public job-board XML feed.

- ``GET /roles/{role_id}/distribution`` (recruiter-auth): the deterministic
  distribution artefacts for a PUBLISHED role — a LinkedIn post, share-intent
  URLs, and the org careers-feed URL. An unpublished role returns
  ``{"published": false}`` (200) so the UI can prompt to publish.
- ``GET /careers/{slug}/feed.xml`` (no auth, mounted under ``/api/v1/public``):
  a ``JobPosting``-schema XML feed built from the SAME open job pages the public
  careers board serves, for Indeed / Google Jobs to pull.

Everything here points at the role's EXISTING public job page (``/job/{token}``)
— NO LinkedIn API, scraping, or automation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_optional_current_user
from ...domains.assessments_runtime.role_support import get_role
from ...models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
from ...models.organization import Organization
from ...models.role import Role
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db
from ...services.distribution_service import (
    build_distribution_artefacts,
    build_job_posting_feed_xml,
)

router = APIRouter(tags=["Distribution"])
public_router = APIRouter(prefix="/api/v1/public", tags=["Distribution"])


def _job_page_url(token: str) -> str:
    """Public job-page URL (``/job/{token}``) on the FRONTEND origin — the same
    URL the recruiter already shares. Relative when FRONTEND_URL is empty."""
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/job/{token}" if base else f"/job/{token}"


def _feed_url(slug: str | None) -> str | None:
    """The org careers-feed URL boards pull, on the BACKEND origin. ``None`` when
    the org has no slug (its careers board is unreachable, so is its feed)."""
    slug = (slug or "").strip()
    if not slug:
        return None
    base = (settings.BACKEND_URL or "").rstrip("/")
    path = f"/api/v1/public/careers/{slug}/feed.xml"
    return f"{base}{path}" if base else path


def _open_page_for_role(db: Session, role: Role) -> JobPage | None:
    """The role's OPEN public job page (role → brief → page), newest first, or
    ``None`` when the role was never published (no open page)."""
    return (
        db.query(JobPage)
        .join(RoleBrief, RoleBrief.id == JobPage.brief_id)
        .filter(
            RoleBrief.role_id == role.id,
            JobPage.organization_id == role.organization_id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
        )
        .order_by(JobPage.published_at.desc(), JobPage.id.desc())
        .first()
    )


@router.get("/roles/{role_id}/distribution")
def get_role_distribution(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Distribution artefacts for a published role. 404 for a foreign role;
    ``{"published": false}`` when the role has no open public job page yet."""
    role = get_role(role_id, current_user.organization_id, db)
    page = _open_page_for_role(db, role)
    if page is None:
        return {"published": False}
    org = db.query(Organization).filter(Organization.id == role.organization_id).first()
    return build_distribution_artefacts(
        page,
        apply_url=_job_page_url(page.token),
        feed_url=_feed_url(org.slug if org else None),
        org_name=org.name if org else None,
    )


@public_router.get("/careers/{slug}/feed.xml")
def careers_feed(
    slug: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """The org's public careers feed (``JobPosting`` XML) — same open pages the
    careers board serves. An unknown slug or an empty board returns a valid,
    empty feed (never a 404/500), so a board polling it never sees an error."""
    slug = (slug or "").strip()
    org = (
        db.query(Organization).filter(Organization.slug == slug).first()
        if slug
        else None
    )
    pages: list[JobPage] = (
        db.query(JobPage)
        .filter(
            JobPage.organization_id == org.id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
        )
        .order_by(JobPage.published_at.desc(), JobPage.id.desc())
        .all()
        if org is not None
        else []
    )
    xml = build_job_posting_feed_xml(
        org_name=org.name if org else None,
        feed_self_url=_feed_url(org.slug if org else slug),
        pages=pages,
        apply_url_for=lambda page: _job_page_url(page.token),
    )
    return Response(content=xml, media_type="application/xml")
