"""Candidate identity resolution for native intake (public apply).

Native intake (public apply) can collide with a candidate already synced from an
ATS. ``resolve_candidate`` finds an existing candidate in the org by deterministic
keys (email, then normalized phone) so a repeat applicant reuses their row instead
of forking a duplicate.

Scope note (E1): the ats branch also carried ``merge_candidates`` (fold a
duplicate into a primary). It had ZERO callers and public apply does not need it —
resolve-or-create is the whole contract here — so it is deliberately NOT ported.
The dedup keys below mirror the Workable sync's matcher, so a native applicant who
later syncs from the ATS resolves to the same person.

All lookups are org-scoped and ignore soft-deleted rows.
"""
from __future__ import annotations

import re

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ..models.candidate import Candidate

_NON_DIGITS = re.compile(r"\D+")


def normalize_phone(raw: str | None) -> str | None:
    """Last 9 digits of a phone number, or None if under 9 — mirrors
    ``sync_service._normalize_phone_for_match`` so native + synced keys match."""
    digits = _NON_DIGITS.sub("", raw or "")
    return digits[-9:] if len(digits) >= 9 else None


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
