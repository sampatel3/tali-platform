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
from ...services.ats_role_lifecycle import ats_job_lifecycle
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
    # The "About the company" blurb is ORG-level boilerplate (set once in
    # Settings / auto-derived), so fall back to it on EVERY requisition whose
    # brief hasn't captured its own — render-time only, not persisted — so the
    # About-us section fills even on requisitions created before it was set.
    custom_fields = dict(brief.custom_fields or {})
    org_blurb = (getattr(org, "company_blurb", None) or "").strip() if org else ""
    if org_blurb and not str(custom_fields.get("company_description") or "").strip():
        custom_fields["company_description"] = org_blurb
    payload["custom_fields"] = custom_fields
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
    # Provider-neutral ATS metadata lets the requisition UI describe the same
    # adoption flow for Workable and Bullhorn while the legacy ids remain for
    # older clients.
    role = brief.role
    ats_lifecycle = ats_job_lifecycle(role)
    payload["job"] = (
        {
            "role_id": role.id,
            "name": role.name,
            "job_status": role.job_status,
            "workable_job_id": role.workable_job_id,
            "bullhorn_job_order_id": role.bullhorn_job_order_id,
            "ats_provider": ats_lifecycle.provider,
            "external_job_id": ats_lifecycle.external_job_id,
            "external_job_state": ats_lifecycle.external_job_state,
            "external_job_live": ats_lifecycle.external_job_live,
        }
        if role
        else None
    )
    return payload


def _ats_spec(jd_markdown: str, ref_code: str) -> str:
    """Return an ATS-portable job specification carrying the Taali link key.

    Workable and Bullhorn imports both scan this visible reference and adopt the
    already-published Taali role instead of creating a duplicate.  The native
    Taali job never depends on this optional external-distribution bridge.
    """
    body = (jd_markdown or "").rstrip()
    ref_line = (
        f"_Taali ref: {ref_code} — please keep this line so this role links "
        f"back to your Taali requisition._"
    )
    return f"{body}\n\n---\n{ref_line}\n" if body else f"{ref_line}\n"


def _workable_spec(jd_markdown: str, ref_code: str) -> str:
    """Backward-compatible alias for clients using the old response key."""

    return _ats_spec(jd_markdown, ref_code)


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
