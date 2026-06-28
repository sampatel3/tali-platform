"""Requisition intake — WARM-START prefill (deterministic, no LLM).

Recency-biased prefill for a NEW requisition, drawn from the org's history so a
brand-new brief already knows the recurring facts (location / workplace /
employment / department):

  * ``warm_start_fields`` — brief-first, then the org's REAL ``Role`` history.
  * ``warm_start_from_roles`` — derive the fields from ``roles.workable_job_data``
    (normalised to the requisition template's labels).
  * ``recent_role_titles`` — recent brief titles for the agent's prompt context.
  * ``seed_opening_message`` — the single deterministic opening assistant turn.

Split out of ``requisition_chat_service`` (the turn engine), which re-exports
these names so the public import path is unchanged.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.role_brief import RoleBrief
from .requisition_chat_capture import (
    _first_required_field,
    _is_empty,
    _select_options,
    opening_message,
)

# Warm-start: the brief columns we prefill on a new requisition from the org's
# recent specs (location/workplace/employment/department recur across roles).
_WARM_START_FIELDS = (
    "location_city",
    "location_country",
    "workplace_type",
    "employment_type",
    "department",
)
# How many recent role titles we surface to the agent as warm-start context.
_RECENT_ROLE_TITLES = 5

# Warm-start from the org's REAL history lives in ``roles.workable_job_data``
# (the structured Workable job payload). Its ``workplace_type`` /
# ``employment_type`` come in Workable's snake_case vocabulary, but the
# requisition template's select OPTIONS are the nice human labels — normalise
# one to the other (case/format-insensitive) so a prefilled value is a valid
# template option and the gap engine / select chips treat it as captured.
_WORKPLACE_TYPE_LABELS: dict[str, str] = {
    "onsite": "Onsite",
    "on_site": "Onsite",
    "on-site": "Onsite",
    "office": "Onsite",
    "hybrid": "Hybrid",
    "remote": "Remote",
}
_EMPLOYMENT_TYPE_LABELS: dict[str, str] = {
    "full_time": "Full-time",
    "full-time": "Full-time",
    "fulltime": "Full-time",
    "part_time": "Part-time",
    "part-time": "Part-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "contractor": "Contract",
    "temporary": "Temporary",
    "temp": "Temporary",
}


def _warm_start_from_briefs(
    db: Session, organization_id: int, exclude_brief_id: Optional[int] = None
) -> dict[str, Any]:
    """The most-recent non-empty value for each warm-start field across the org's
    RoleBriefs (recency-biased).

    For each of ``location_city / location_country / workplace_type /
    employment_type / department`` independently, walk the org's briefs newest
    first (``created_at`` desc, then ``id`` desc) and take the first non-empty
    value. Optionally exclude one brief (the just-created one). Returns only the
    keys that resolved to a value.
    """
    query = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == organization_id)
        .order_by(RoleBrief.created_at.desc(), RoleBrief.id.desc())
    )
    if exclude_brief_id is not None:
        query = query.filter(RoleBrief.id != exclude_brief_id)

    resolved: dict[str, Any] = {}
    remaining = set(_WARM_START_FIELDS)
    for prior in query.all():
        if not remaining:
            break
        for field in list(remaining):
            value = getattr(prior, field, None)
            if not _is_empty(value):
                resolved[field] = value
                remaining.discard(field)
    return resolved


def _norm_select(value: Any, labels: dict[str, str]) -> Optional[str]:
    """Normalise a raw select value to its template label via ``labels`` (a
    lower-cased lookup). Tolerates already-nice values (e.g. ``"Hybrid"``,
    ``"Full-time"``) by matching case-insensitively against the label set too.
    Returns ``None`` for empty / unrecognised input."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    mapped = labels.get(key)
    if mapped is not None:
        return mapped
    # Already a nice label (or differently-cased one)? Accept it verbatim.
    for label in labels.values():
        if label.lower() == key:
            return label
    return None


def _warm_start_from_job_data(wjd: dict[str, Any]) -> dict[str, Any]:
    """Pull the warm-start fields out of ONE role's ``workable_job_data`` payload,
    normalised to the requisition template's shapes. Robust to missing keys, a
    non-dict ``location``, and unexpected value types — a field that can't be
    derived is simply absent from the result (never raises)."""
    out: dict[str, Any] = {}

    workplace = _norm_select(wjd.get("workplace_type"), _WORKPLACE_TYPE_LABELS)
    if workplace is not None:
        out["workplace_type"] = workplace

    employment = _norm_select(wjd.get("employment_type"), _EMPLOYMENT_TYPE_LABELS)
    if employment is not None:
        out["employment_type"] = employment

    # Department is often null on Workable jobs — only take a non-empty string.
    department = wjd.get("department")
    if isinstance(department, str) and department.strip():
        out["department"] = department.strip()

    # Location: prefer the structured city/country; fall back to splitting the
    # human "City, …, Country" ``location_str`` when the structured keys are
    # absent (matches the prod shape, where location_str is always present).
    location = wjd.get("location")
    city = country = None
    location_str = None
    if isinstance(location, dict):
        raw_city = location.get("city") or location.get("city_name")
        raw_country = location.get("country") or location.get("country_name")
        if isinstance(raw_city, str) and raw_city.strip():
            city = raw_city.strip()
        if isinstance(raw_country, str) and raw_country.strip():
            country = raw_country.strip()
        raw_str = location.get("location_str")
        if isinstance(raw_str, str) and raw_str.strip():
            location_str = raw_str.strip()
    elif isinstance(location, str) and location.strip():
        location_str = location.strip()

    if (city is None or country is None) and location_str:
        parts = [p.strip() for p in location_str.split(", ") if p.strip()]
        if parts:
            if city is None:
                city = parts[0]
            if country is None and len(parts) > 1:
                country = parts[-1]

    if city:
        out["location_city"] = city
    if country:
        out["location_country"] = country

    return out


def warm_start_from_roles(
    db: Session, organization_id: int
) -> dict[str, Any]:
    """Recency-biased warm-start derived from the org's REAL specs — its
    non-deleted ``Role`` rows' ``workable_job_data`` (the structured Workable job
    payload), NOT the near-empty ``role_briefs`` table.

    Walks the org's roles newest first (``created_at`` desc, then ``id`` desc)
    and, for each warm-start field independently
    (``workplace_type / employment_type / location_city / location_country /
    department``), takes the first non-empty value — normalised to the
    requisition template's labels (e.g. ``"hybrid"`` → ``"Hybrid"``,
    ``"full_time"`` → ``"Full-time"``). Robust to missing/odd ``workable_job_data``
    (such roles just contribute nothing). Deterministic; no LLM. Returns only the
    keys that resolved to a value.
    """
    query = (
        db.query(Role)
        .filter(
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.created_at.desc(), Role.id.desc())
    )

    resolved: dict[str, Any] = {}
    remaining = set(_WARM_START_FIELDS)
    for role in query.all():
        if not remaining:
            break
        wjd = role.workable_job_data
        if not isinstance(wjd, dict) or not wjd:
            continue
        derived = _warm_start_from_job_data(wjd)
        for field in list(remaining):
            value = derived.get(field)
            if not _is_empty(value):
                resolved[field] = value
                remaining.discard(field)
    return resolved


def warm_start_fields(
    db: Session, organization_id: int, exclude_brief_id: Optional[int] = None
) -> dict[str, Any]:
    """Recency-biased prefill for a NEW requisition, deterministic (no LLM).

    Combines two sources, brief-first: a recruiter's own recent requisitions
    (``RoleBrief`` rows) are the most relevant, so they win per field; any
    warm-start field still empty is then filled from the org's REAL history — its
    recent ``Role`` rows' ``workable_job_data`` (see ``warm_start_from_roles``).
    In practice ``role_briefs`` is near-empty, so most fields come from roles.

    Returns only the keys that resolved to a value (across either source).
    """
    resolved = _warm_start_from_briefs(
        db, organization_id, exclude_brief_id=exclude_brief_id
    )
    if set(resolved) >= set(_WARM_START_FIELDS):
        return resolved
    for field, value in warm_start_from_roles(db, organization_id).items():
        resolved.setdefault(field, value)
    return resolved


def recent_role_titles(
    db: Session, organization_id: int, exclude_brief_id: Optional[int] = None
) -> list[str]:
    """Up to ``_RECENT_ROLE_TITLES`` recent non-empty brief titles for the org
    (newest first), for warm-start context in the agent's system prompt."""
    query = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == organization_id)
        .order_by(RoleBrief.created_at.desc(), RoleBrief.id.desc())
    )
    if exclude_brief_id is not None:
        query = query.filter(RoleBrief.id != exclude_brief_id)

    titles: list[str] = []
    for prior in query.all():
        if len(titles) >= _RECENT_ROLE_TITLES:
            break
        title = (prior.title or "").strip()
        if title:
            titles.append(title)
    return titles


def seed_opening_message(brief: RoleBrief, template: dict[str, Any]) -> None:
    """Set ``brief.messages`` to the single deterministic OPENING assistant turn.
    Mutates in place (does not flush)."""
    brief.messages = [
        {
            "role": "assistant",
            "content": opening_message(template),
            "attachments": [],
            "suggested_replies": _select_options(_first_required_field(template)),
        }
    ]
