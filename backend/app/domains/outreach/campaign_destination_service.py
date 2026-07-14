"""Truthful application destinations for outreach campaigns."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ...models.role import Role


def resolve_job_page_token(db: Session, role_id: Optional[int]) -> Optional[str]:
    """Return the role's published open native JobPage token, if one exists."""
    if role_id is None:
        return None
    from ...models.job_page import JOB_PAGE_STATUS_OPEN, JobPage
    from ...models.role_brief import RoleBrief

    page = (
        db.query(JobPage)
        .join(RoleBrief, RoleBrief.id == JobPage.brief_id)
        .filter(
            RoleBrief.role_id == role_id,
            JobPage.status == JOB_PAGE_STATUS_OPEN,
        )
        .order_by(JobPage.id.desc())
        .first()
    )
    return page.token if page is not None else None


def _validated_https_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return text


def resolve_campaign_destination(
    db: Session, role_id: Optional[int]
) -> dict[str, Any]:
    """Resolve an honest apply destination without inventing connector support."""
    if role_id is None:
        return {
            "status": "application_destination_required",
            "provider": None,
            "job_page_token": None,
            "destination_url": None,
        }
    role = db.query(Role).filter(Role.id == int(role_id)).first()
    if role is None:
        return {
            "status": "application_destination_required",
            "provider": None,
            "job_page_token": None,
            "destination_url": None,
        }

    token = resolve_job_page_token(db, int(role.id))
    if token:
        return {
            "status": "ready",
            "provider": "native",
            "job_page_token": token,
            "destination_url": None,
        }

    if role.workable_job_id:
        data = role.workable_job_data if isinstance(role.workable_job_data, dict) else {}
        url = _validated_https_url(
            data.get("application_url")
            or data.get("applicationUrl")
            or data.get("url")
        )
        return {
            "status": "ready" if url else "application_destination_required",
            "provider": "workable",
            "job_page_token": None,
            "destination_url": url,
        }

    if role.bullhorn_job_order_id:
        data = role.bullhorn_job_data if isinstance(role.bullhorn_job_data, dict) else {}
        url = _validated_https_url(
            data.get("application_url")
            or data.get("applicationUrl")
            or data.get("publicUrl")
            or data.get("jobPostingURL")
        )
        return {
            "status": "ready" if url else "application_destination_required",
            "provider": "bullhorn",
            "job_page_token": None,
            "destination_url": url,
        }

    # A native role without a published page may still capture a low-stakes
    # click, but cannot claim that the candidate applied.
    return {
        "status": "interest_capture_only",
        "provider": "native",
        "job_page_token": None,
        "destination_url": None,
    }


def default_brief(role_name: Optional[str], job_spec_text: Optional[str]) -> str:
    """Build an instant deterministic starter brief from role context."""
    title = (role_name or "the role").strip() or "the role"
    lines = [f"Reaching out about our {title} opening."]
    summary = (job_spec_text or "").strip()
    if summary:
        lines.append(summary[:600])
    return "\n\n".join(lines)
