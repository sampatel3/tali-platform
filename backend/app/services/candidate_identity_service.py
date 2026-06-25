"""P1: candidate identity resolution + cross-source merge.

Native intake (public apply) can collide with a candidate already synced from
Workable (same person, maybe a different email). ``resolve_candidate`` finds an
existing candidate by deterministic keys; ``merge_candidates`` folds a duplicate
into a primary, reassigning applications and backfilling empty fields. This is
the prerequisite the roadmap flags as must-precede the public apply endpoint.

All mutators flush but do NOT commit — the caller owns the transaction.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication

_NON_DIGITS = re.compile(r"\D+")

# Fields copied from the duplicate onto the primary when the primary's value is
# empty (never overwrites a set value; ``email`` is the primary's identity and is
# intentionally excluded). workable_candidate_id is included so a native primary
# inherits the Workable link from a synced duplicate.
_FILLABLE_FIELDS = (
    "full_name",
    "phone",
    "phone_normalized",
    "work_email",
    "company_name",
    "lead_source",
    "headline",
    "image_url",
    "location_city",
    "location_country",
    "profile_url",
    "social_profiles",
    "tags",
    "skills",
    "education_entries",
    "experience_entries",
    "summary",
    "cv_file_url",
    "cv_filename",
    "cv_text",
    "cv_sections",
    "workable_candidate_id",
    "workable_data",
)


def normalize_phone(raw: str | None) -> str | None:
    """Last 9 digits of a phone number, or None if under 9 — mirrors
    sync_service._normalize_phone_for_match so native + synced keys match."""
    digits = _NON_DIGITS.sub("", raw or "")
    return digits[-9:] if len(digits) >= 9 else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_candidate(
    db: Session,
    organization_id: int,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> Candidate | None:
    """Find an existing (non-deleted) candidate in the org by deterministic keys:
    email (case-insensitive) first, then normalized phone. None if no match."""
    email_clean = (email or "").strip().lower()
    if email_clean:
        match = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == organization_id,
                sa_func.lower(Candidate.email) == email_clean,
                Candidate.deleted_at.is_(None),
            )
            .order_by(Candidate.id)
            .first()
        )
        if match:
            return match
    phone_key = normalize_phone(phone)
    if phone_key:
        match = (
            db.query(Candidate)
            .filter(
                Candidate.organization_id == organization_id,
                Candidate.phone_normalized == phone_key,
                Candidate.deleted_at.is_(None),
            )
            .order_by(Candidate.id)
            .first()
        )
        if match:
            return match
    return None


def merge_candidates(
    db: Session, *, primary: Candidate, duplicate: Candidate
) -> Candidate:
    """Fold ``duplicate`` into ``primary`` (same org). Reassigns the duplicate's
    applications to the primary, EXCEPT where the primary already has an
    application for that role — there the duplicate's application is soft-deleted
    in place (the unique (candidate_id, role_id) constraint spans soft-deleted
    rows, so we never move a colliding row). Backfills empty primary fields, then
    soft-deletes the duplicate."""
    if primary.id == duplicate.id:
        return primary
    if primary.organization_id != duplicate.organization_id:
        raise HTTPException(
            status_code=422, detail="Cannot merge candidates across organizations"
        )

    # Every role the primary already has an application for (incl. soft-deleted,
    # because the unique constraint is not filtered by deleted_at).
    primary_role_ids = {
        rid
        for (rid,) in db.query(CandidateApplication.role_id)
        .filter(CandidateApplication.candidate_id == primary.id)
        .all()
    }
    for app in (
        db.query(CandidateApplication)
        .filter(CandidateApplication.candidate_id == duplicate.id)
        .all()
    ):
        if app.role_id in primary_role_ids:
            if app.deleted_at is None:
                app.deleted_at = _utcnow()  # collision: keep the primary's app
        else:
            app.candidate_id = primary.id
            primary_role_ids.add(app.role_id)

    for field in _FILLABLE_FIELDS:
        if not getattr(primary, field, None) and getattr(duplicate, field, None):
            setattr(primary, field, getattr(duplicate, field))

    duplicate.deleted_at = _utcnow()
    db.flush()
    return primary
