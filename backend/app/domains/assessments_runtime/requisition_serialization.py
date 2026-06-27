"""Requisition brief serialization for the recruiter API.

Split out of ``requisition_routes`` (to keep the route module under the
file-size gate). Builds the full brief payload the recruiter UI renders —
every v1 field plus custom_fields, messages, live gaps, completeness, the
consultancy economics, and the public job-page / client-intake / careers links.
The route module imports ``_serialize_brief`` plus the two URL helpers it needs
for the publish / client-link endpoints.
"""
from __future__ import annotations

from typing import Any, Optional

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


_BRIEF_FIELDS = (
    "id",
    "role_id",
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
    return payload
