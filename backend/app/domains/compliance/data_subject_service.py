"""GDPR-style data-subject requests — access export + erasure.

The request row is the durable compliance evidence; it survives an erased
candidate. ``build_access_export`` gathers a portable copy of a candidate's
data; ``fulfill_erasure`` scrubs the direct PII on the candidate row and
soft-deletes it. Mutators flush but do NOT commit.

SCOPE — erasure scrubs the ``candidates`` row (the primary PII surface), which
on this platform now includes the raw ATS payload columns ``workable_data`` /
``bullhorn_data`` (full third-party PII), plus company/tags/skills/comments.
See ``_ERASE_FIELDS`` for the exact, enumerated list.

OUT OF SCOPE (documented, NOT silently assumed complete) — PII that echoes into
OTHER tables is a broader sweep to land against staging, deliberately not
cascaded here:
  - ``candidate_applications`` — cv_text, cv_file_url, cv_filename, cv_sections,
    screening_answers (may echo free-text PII), notes.
  - Interview records — ``application_interviews`` / ``interview_feedback``
    (free-text notes about the person).
  - Report snapshots — top-candidate ``rpt_`` reports and submittal packs
    (frozen shortlists that embed candidate name/CV text at mint time).
  - Event / audit payloads — ``candidate_application_event`` reason/detail and
    the immutable audit trail (retained deliberately as compliance evidence).
  - Outreach — ``prospects`` reference candidates via ``candidate_id`` and carry
    their own name/email; ``outreach_campaign`` recipients likewise. Erasing the
    candidate does NOT scrub a linked prospect row.
  - Assessments + assessment artifacts (candidate answers / uploaded CV).
  - Re-import: a still-connected ATS (Workable / Bullhorn) can re-sync an erased
    candidate. Suppressing re-import is a source-system concern, out of scope here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.data_subject_request import (
    DSR_STATUS_COMPLETED,
    DSR_STATUS_PENDING,
    DSR_STATUS_REJECTED,
    DSR_TYPE_ACCESS,
    DSR_TYPE_ERASURE,
    DSR_TYPES,
    DataSubjectRequest,
)

# Candidate columns scrubbed on erasure — direct identifiers, free-text PII, the
# raw third-party ATS payloads, and the external identity links. Enumerated
# against the CURRENT ``candidates`` model (not the older ATS-build model, which
# missed ``workable_data`` — the raw ATS JSON with full PII — the vulnerability
# this fix closes). Timestamps, boolean flags (marketing_consent), lead_source
# provenance, and the recruiter's job-spec upload are deliberately retained.
_ERASE_FIELDS = (
    # Direct identity
    "email", "full_name", "phone", "phone_normalized", "work_email", "position",
    "profile_url", "image_url", "headline", "summary", "location_city",
    "location_country",
    # CV surfaces on the candidate row
    "cv_file_url", "cv_filename", "cv_text", "cv_sections",
    # Structured profile PII
    "social_profiles", "education_entries", "experience_entries",
    "tags", "skills", "company_name", "company_size",
    # External ATS identity + raw payloads (the reviewed fix — these carry full
    # third-party PII / free-text about the person)
    "workable_candidate_id", "workable_data", "workable_comments",
    "workable_activities", "bullhorn_candidate_id", "bullhorn_data",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_request(
    db: Session,
    org_id: int,
    *,
    request_type: str,
    subject_email: Optional[str] = None,
    candidate_id: Optional[int] = None,
    requested_by_user_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> DataSubjectRequest:
    if request_type not in DSR_TYPES:
        raise HTTPException(
            status_code=422, detail=f"Unknown request_type={request_type!r}"
        )
    if not (subject_email or candidate_id):
        raise HTTPException(
            status_code=422, detail="A subject_email or candidate_id is required"
        )
    req = DataSubjectRequest(
        organization_id=org_id,
        request_type=request_type,
        subject_email=(subject_email or "").strip().lower() or None,
        candidate_id=candidate_id,
        requested_by_user_id=requested_by_user_id,
        notes=notes,
        status=DSR_STATUS_PENDING,
    )
    db.add(req)
    db.flush()
    return req


def list_requests(db: Session, org_id: int) -> List[DataSubjectRequest]:
    return (
        db.query(DataSubjectRequest)
        .filter(DataSubjectRequest.organization_id == org_id)
        .order_by(DataSubjectRequest.id.desc())
        .all()
    )


def get_request(db: Session, org_id: int, request_id: int) -> DataSubjectRequest:
    req = (
        db.query(DataSubjectRequest)
        .filter(
            DataSubjectRequest.id == request_id,
            DataSubjectRequest.organization_id == org_id,
        )
        .first()
    )
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


def _resolve_candidate(
    db: Session, org_id: int, req: DataSubjectRequest
) -> Optional[Candidate]:
    q = db.query(Candidate).filter(Candidate.organization_id == org_id)
    if req.candidate_id:
        return q.filter(Candidate.id == req.candidate_id).first()
    if req.subject_email:
        # Case-insensitive on BOTH sides: create_request lowercases the submitted
        # address, but imported/legacy candidate rows can hold mixed-case emails —
        # an erasure must not miss `Person@Example.com`. Mirrors the identity
        # resolver (candidate_identity_service.resolve_candidate).
        return q.filter(
            sa_func.lower(Candidate.email) == req.subject_email.strip().lower()
        ).first()
    return None


def build_access_export(db: Session, candidate: Candidate) -> Dict[str, Any]:
    """A portable copy of the candidate's data (profile + application history)."""
    apps = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.candidate_id == candidate.id)
        .all()
    )
    return {
        "candidate": {
            "id": candidate.id,
            "email": candidate.email,
            "full_name": candidate.full_name,
            "phone": candidate.phone,
            "location_city": candidate.location_city,
            "location_country": candidate.location_country,
            "headline": candidate.headline,
            "summary": candidate.summary,
        },
        "applications": [
            {
                "id": a.id,
                "role_id": a.role_id,
                "pipeline_stage": a.pipeline_stage,
                "application_outcome": a.application_outcome,
                "source": a.source,
            }
            for a in apps
        ],
    }


def fulfill_erasure(db: Session, candidate: Candidate) -> None:
    """Scrub the candidate's direct PII (see ``_ERASE_FIELDS``) and soft-delete
    the row. Cross-table PII is out of scope — see the module docstring."""
    for field in _ERASE_FIELDS:
        if hasattr(candidate, field):
            setattr(candidate, field, None)
    candidate.deleted_at = _utcnow()
    db.flush()


def fulfill_request(
    db: Session, org_id: int, req: DataSubjectRequest
) -> Dict[str, Any]:
    """Execute a pending request. Access → returns the export payload; erasure →
    scrubs the resolved candidate. Either way the request is marked completed.
    Returns a result dict (``export`` present for access requests)."""
    if req.status != DSR_STATUS_PENDING:
        raise HTTPException(status_code=409, detail="Request is not pending")
    candidate = _resolve_candidate(db, org_id, req)

    result: Dict[str, Any] = {"request_type": req.request_type}
    if req.request_type == DSR_TYPE_ACCESS:
        if candidate is None:
            raise HTTPException(
                status_code=404, detail="No candidate matched the request"
            )
        result["export"] = build_access_export(db, candidate)
    elif req.request_type == DSR_TYPE_ERASURE:
        # A no-match erasure still completes (nothing to erase) — the log stands.
        if candidate is not None:
            fulfill_erasure(db, candidate)
        result["erased"] = candidate is not None

    req.status = DSR_STATUS_COMPLETED
    req.completed_at = _utcnow()
    db.flush()
    return result


def reject_request(
    db: Session, req: DataSubjectRequest, *, reason: str
) -> DataSubjectRequest:
    if req.status != DSR_STATUS_PENDING:
        raise HTTPException(status_code=409, detail="Request is not pending")
    req.status = DSR_STATUS_REJECTED
    req.notes = reason
    req.completed_at = _utcnow()
    db.flush()
    return req
