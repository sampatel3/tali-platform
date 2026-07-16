"""Prospect CRUD + CSV import — the sourced-lead store for outreach.

A prospect is a sourced outreach target (CSV import, manual add) that isn't yet
a full Candidate. Everything here is org-scoped via the recruiter session, and
every list surfaces the suppression state (bulk-checked, no N+1) so recruiters
never queue an unsubscribed/bounced address for a campaign.

No LLM calls, no metering, no send machinery — that's the next PR.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.candidate import Candidate
from ...models.prospect import (
    PROSPECT_STATUS_ARCHIVED,
    PROSPECT_STATUSES,
    Prospect,
)
from ...models.user import User
from ...platform.database import get_db
from ...services.email_suppression_service import normalize_email, suppressed_set


router = APIRouter(prefix="/prospects", tags=["Prospects"])

# CSV import is a curated shortlist, not a data dump.
_MAX_IMPORT_ROWS = 500

# Recognised CSV columns (headers matched case-insensitively).
_CSV_REQUIRED = ("full_name", "email")
_CSV_OPTIONAL = ("phone", "position", "location", "linkedin_url", "notes")


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _serialize(prospect: Prospect, suppressed_reason: str | None) -> dict[str, Any]:
    return {
        "id": prospect.id,
        "full_name": prospect.full_name,
        "email": prospect.email,
        "phone": prospect.phone,
        "position": prospect.position,
        "location": prospect.location,
        "linkedin_url": prospect.linkedin_url,
        "notes": prospect.notes,
        "source_strategy": prospect.source_strategy,
        "source_name": prospect.source_name,
        "status": prospect.status,
        "candidate_id": prospect.candidate_id,
        "created_at": prospect.created_at.isoformat() if prospect.created_at else None,
        "updated_at": prospect.updated_at.isoformat() if prospect.updated_at else None,
        # Suppression reason (unsubscribed | bounced | complained | manual) or
        # None — surfaced so the UI can flag an un-mailable prospect.
        "suppressed": suppressed_reason,
    }


class _EmailProbe(BaseModel):
    email: EmailStr


def _valid_email(email: str) -> bool:
    """Same validator as the manual-create path (pydantic EmailStr) so CSV
    imports can't mint mail-able prospects with addresses like ``bad@``."""
    try:
        _EmailProbe(email=email)
        return True
    except Exception:
        return False


def _link_candidate_id(db: Session, organization_id: int, email: str) -> int | None:
    """Return the id of an in-org candidate with this (normalized) email, if any."""
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == organization_id,
            # Candidate emails can be stored with submitted casing; the
            # prospect side is already normalized (lowercased).
            func.lower(Candidate.email) == email,
        )
        .first()
    )
    return candidate.id if candidate is not None else None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class CreateProspectPayload(BaseModel):
    full_name: str = Field(..., min_length=1)
    email: EmailStr
    phone: str | None = None
    position: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    source_name: str | None = None


@router.post("")
def create_prospect(
    payload: CreateProspectPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    email = normalize_email(payload.email)

    existing = (
        db.query(Prospect)
        .filter(Prospect.organization_id == org_id, Prospect.email == email)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="A prospect with this email already exists")

    prospect = Prospect(
        organization_id=org_id,
        full_name=payload.full_name.strip(),
        email=email,
        phone=payload.phone,
        position=payload.position,
        location=payload.location,
        linkedin_url=payload.linkedin_url,
        notes=payload.notes,
        source_strategy="sourced",
        source_name=(payload.source_name or "manual"),
        candidate_id=_link_candidate_id(db, org_id, email),
        created_by_user_id=current_user.id,
    )
    db.add(prospect)
    db.commit()
    db.refresh(prospect)

    reason = suppressed_set(db, emails=[email], organization_id=org_id).get(email)
    return _serialize(prospect, reason)


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


def _normalize_headers(fieldnames: list[str] | None) -> dict[str, str]:
    """Map lowercased/trimmed header → recognised column name."""
    recognised = set(_CSV_REQUIRED) | set(_CSV_OPTIONAL)
    mapping: dict[str, str] = {}
    for raw in fieldnames or []:
        key = str(raw or "").strip().lower()
        if key in recognised:
            mapping[raw] = key
    return mapping


@router.post("/import")
async def import_prospects(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk-import prospects from a CSV. Valid rows commit; invalid rows are
    reported. Never partial-fails.

    Columns: ``full_name``, ``email`` (required) + optional ``phone``,
    ``position``, ``location``, ``linkedin_url``, ``notes``. Headers are matched
    case-insensitively. Beyond 500 data rows → 413.
    """
    org_id = current_user.organization_id

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    header_map = _normalize_headers(reader.fieldnames)
    if "full_name" not in header_map.values() or "email" not in header_map.values():
        raise HTTPException(
            status_code=400,
            detail="CSV must have 'full_name' and 'email' columns",
        )

    rows = list(reader)
    if len(rows) > _MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"CSV has more than {_MAX_IMPORT_ROWS} rows",
        )

    # Existing prospect emails for this org that could collide with this file.
    # The import is capped at 500 rows, so this bounded IN query avoids loading
    # every prospect email in a large workspace while retaining one lookup.
    email_header = next(
        raw for raw, normalised in header_map.items() if normalised == "email"
    )
    csv_emails = {
        email
        for row in rows
        if (email := normalize_email((row.get(email_header) or "").strip()))
    }
    existing_emails: set[str] = set()
    if csv_emails:
        existing_emails = {
            e
            for (e,) in db.query(Prospect.email)
            .filter(
                Prospect.organization_id == org_id,
                Prospect.email.in_(csv_emails),
            )
            .all()
        }

    created = 0
    linked = 0
    duplicates_in_file = 0
    already_prospects = 0
    invalid_rows: list[dict[str, Any]] = []
    seen_in_file: set[str] = set()
    to_add: list[Prospect] = []

    for idx, row in enumerate(rows, start=1):
        # Re-key the row onto recognised column names.
        record = {col: (row.get(orig) or "").strip() for orig, col in header_map.items()}
        full_name = record.get("full_name", "")
        email = normalize_email(record.get("email", ""))

        if not full_name:
            invalid_rows.append({"row": idx, "reason": "missing full_name"})
            continue
        if not email or not _valid_email(email):
            invalid_rows.append({"row": idx, "reason": "missing or invalid email"})
            continue
        if email in seen_in_file:
            duplicates_in_file += 1
            continue
        seen_in_file.add(email)
        if email in existing_emails:
            already_prospects += 1
            continue

        candidate_id = _link_candidate_id(db, org_id, email)
        if candidate_id is not None:
            linked += 1
        to_add.append(
            Prospect(
                organization_id=org_id,
                full_name=full_name,
                email=email,
                phone=record.get("phone") or None,
                position=record.get("position") or None,
                location=record.get("location") or None,
                linkedin_url=record.get("linkedin_url") or None,
                notes=record.get("notes") or None,
                source_strategy="sourced",
                source_name=f"csv:{file.filename}" if file.filename else "csv",
                candidate_id=candidate_id,
                created_by_user_id=current_user.id,
            )
        )
        created += 1

    if to_add:
        db.add_all(to_add)
        db.commit()

    return {
        "created": created,
        "linked_to_existing_candidate": linked,
        "duplicates_in_file": duplicates_in_file,
        "already_prospects": already_prospects,
        "invalid_rows": invalid_rows,
    }


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
def list_prospects(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    query = db.query(Prospect).filter(Prospect.organization_id == org_id)

    if status == "active":
        query = query.filter(Prospect.status != PROSPECT_STATUS_ARCHIVED)
    elif status:
        query = query.filter(Prospect.status == status)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                Prospect.full_name.ilike(like),
                Prospect.email.ilike(like),
                Prospect.position.ilike(like),
            )
        )

    total = query.count()
    prospects = (
        query.order_by(Prospect.created_at.desc(), Prospect.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Bulk suppression check — one query for the whole page, no N+1.
    reasons = suppressed_set(
        db, emails=[p.email for p in prospects], organization_id=org_id
    )
    return {
        "prospects": [_serialize(p, reasons.get(p.email)) for p in prospects],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Update / archive
# ---------------------------------------------------------------------------


class UpdateProspectPayload(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    position: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    status: str | None = None


def _get_owned_prospect(db: Session, prospect_id: int, org_id: int) -> Prospect:
    prospect = (
        db.query(Prospect)
        .filter(Prospect.id == prospect_id, Prospect.organization_id == org_id)
        .first()
    )
    if prospect is None:
        raise HTTPException(status_code=404, detail="Prospect not found")
    return prospect


@router.patch("/{prospect_id}")
def update_prospect(
    prospect_id: int,
    payload: UpdateProspectPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = current_user.organization_id
    prospect = _get_owned_prospect(db, prospect_id, org_id)

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        if data["status"] not in PROSPECT_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
    if "email" in data and data["email"] is not None:
        new_email = normalize_email(data["email"])
        clash = (
            db.query(Prospect)
            .filter(
                Prospect.organization_id == org_id,
                Prospect.email == new_email,
                Prospect.id != prospect.id,
            )
            .first()
        )
        if clash is not None:
            raise HTTPException(status_code=409, detail="Another prospect has this email")
        prospect.email = new_email
        prospect.candidate_id = _link_candidate_id(db, org_id, new_email)
        data.pop("email")

    for field, value in data.items():
        setattr(prospect, field, value)

    db.commit()
    db.refresh(prospect)
    reason = suppressed_set(db, emails=[prospect.email], organization_id=org_id).get(
        prospect.email
    )
    return _serialize(prospect, reason)


@router.delete("/{prospect_id}")
def archive_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete: set status=archived (prospects are retained for audit)."""
    org_id = current_user.organization_id
    prospect = _get_owned_prospect(db, prospect_id, org_id)
    prospect.status = PROSPECT_STATUS_ARCHIVED
    db.commit()
    db.refresh(prospect)
    reason = suppressed_set(db, emails=[prospect.email], organization_id=org_id).get(
        prospect.email
    )
    return _serialize(prospect, reason)
