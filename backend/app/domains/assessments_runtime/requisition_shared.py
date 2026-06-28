"""Shared serialization + lookup helpers for the requisition routers.

The requisition API is split across a few cohesive routers (core CRUD/chat in
``requisition_routes`` plus the publish / client-link / template-settings
surfaces). They all need the same brief serializer, brief/org lookups, and
public-URL builders — those live here so each router imports one source of truth
(no duplication, no circular import: this module imports no router).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.organization import Organization
from ...models.role_brief import RoleBrief
from ...platform.config import settings
from ...services.client_service import compute_margin
from ...services.requisition_chat_service import compute_gaps
from ...services.requisition_template_service import resolve_template


def _job_page_url(token: str) -> str:
    """Public job-page URL. ``/job/{token}`` relative when FRONTEND_URL is empty."""
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/job/{token}" if base else f"/job/{token}"


def _client_intake_url(token: str) -> str:
    """The no-login CLIENT INTAKE share URL. ``/intake/{token}`` relative when
    FRONTEND_URL is empty."""
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/intake/{token}" if base else f"/intake/{token}"


def _careers_url(slug: Optional[str]) -> Optional[str]:
    """The org's PUBLIC careers board URL (``/careers/{slug}``), relative when
    FRONTEND_URL is empty. ``None`` when the org has no slug (unreachable board)."""
    if not slug:
        return None
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}/careers/{slug}" if base else f"/careers/{slug}"


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
_BRIEF_FIELDS = (
    "id",
    "role_id",
    "ref_code",
    "status",
    "source_kind",
    "title",
    "summary",
    "department",
    "location_city",
    "location_country",
    "workplace_type",
    "employment_type",
    "seniority",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "openings",
    "target_start",
    "client_id",
    "client_rate",
    "must_haves",
    "preferred",
    "dealbreakers",
    "success_profile",
    "priorities",
    "tradeoffs",
    "calibration_exemplars",
    "sourcing_signals",
    "assessment_focus",
    "process",
    "evp",
    "agent_state",
    "completeness",
)


def _serialize_brief(brief: RoleBrief, org: Optional[Organization]) -> dict[str, Any]:
    """The full brief payload: every v1 field PLUS custom_fields, messages,
    completeness (0-100), the live ``gaps`` (required template fields still
    empty), and the consultancy economics (client_name + margin/margin_pct)."""
    template = resolve_template(org)
    payload: dict[str, Any] = {k: getattr(brief, k, None) for k in _BRIEF_FIELDS}
    payload["custom_fields"] = brief.custom_fields or {}
    payload["messages"] = brief.messages or []
    payload["completeness"] = int(brief.completeness or 0)
    payload["gaps"] = compute_gaps(brief, template)
    # Recruiter's hand-edited Job spec (stored in agent_state, not a column).
    payload["jd_override"] = (brief.agent_state or {}).get("jd_override")
    # Consultancy: resolve the client name + compute margin (never stored).
    payload["client_name"] = brief.client.name if brief.client else None
    margin, margin_pct = compute_margin(
        brief.client_rate, brief.salary_min, brief.salary_max
    )
    payload["margin"] = margin
    payload["margin_pct"] = margin_pct
    # The brief's published PUBLIC job page (None until first published).
    page = brief.job_page
    payload["job_page"] = (
        {
            "token": page.token,
            "url": _job_page_url(page.token),
            "status": page.status,
            "published_at": page.published_at.isoformat() if page.published_at else None,
        }
        if page
        else None
    )
    # The scoped, no-login CLIENT INTAKE share link (None until the recruiter
    # mints it). The token itself is the only secret — never any economics.
    token = brief.client_intake_token
    payload["client_link"] = (
        {"token": token, "url": _client_intake_url(token)} if token else None
    )
    # The org's PUBLIC careers board (lists every published page). None when the
    # org has no slug; lets the recruiter UI link the board.
    payload["careers_url"] = _careers_url(org.slug if org else None)
    # The INACTIVE Taali job stood up on publish (None until first published).
    # Carries the lifecycle status (draft -> open once the Workable bridge links
    # it) so the requisition UI can show "Inactive job created / now Open".
    role = brief.role
    payload["job"] = (
        {
            "role_id": role.id,
            "name": role.name,
            "job_status": role.job_status,
            "workable_job_id": role.workable_job_id,
        }
        if role
        else None
    )
    return payload


def _workable_spec(jd_markdown: str, ref_code: str) -> str:
    """The recruiter copies THIS into the Workable job description. It's the
    rendered JD with a visible ref line appended — when the job syncs back, the
    read-sync scans the description for ``ref_code`` and adopts the matching
    inactive Taali job (Workable has no job-creation API, so the code rides
    inside the spec the recruiter is already pasting). The wording asks them to
    keep the line; ``find_ref_code`` tolerates any surrounding prose."""
    body = (jd_markdown or "").rstrip()
    ref_line = (
        f"_Taali ref: {ref_code} — please keep this line so this role links "
        f"back to your Taali requisition._"
    )
    return f"{body}\n\n---\n{ref_line}\n" if body else f"{ref_line}\n"


def _get_brief(db: Session, organization_id: int, brief_id: int) -> RoleBrief:
    brief = (
        db.query(RoleBrief)
        .filter(RoleBrief.id == brief_id, RoleBrief.organization_id == organization_id)
        .first()
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return brief


def _org(db: Session, organization_id: int) -> Optional[Organization]:
    return (
        db.query(Organization).filter(Organization.id == organization_id).first()
    )
