"""P5: GDPR-style data-subject requests — access export + erasure.

The request row is the durable compliance evidence; it survives an erased
candidate. ``build_access_export`` gathers a portable copy of a candidate's
data; ``fulfill_erasure`` anonymizes the direct PII on the candidate and
soft-deletes it. Mutators flush but do NOT commit.

NB: erasure here scrubs the ``candidates`` row (the primary PII surface) and
soft-deletes it. A full cascade across every table that may echo PII (CV text
on applications, interview transcripts, agent memory) is a broader sweep to
land against staging — flagged, not silently assumed complete.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
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

# Candidate columns scrubbed on erasure (direct identifiers + free-text PII).
_ERASE_FIELDS = (
    "email", "full_name", "phone", "phone_normalized", "work_email",
    "profile_url", "image_url", "headline", "summary", "location_city",
    "location_country", "cv_file_url", "cv_filename", "cv_text", "cv_sections",
    "social_profiles", "education_entries", "experience_entries",
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
        raise HTTPException(status_code=422, detail=f"Unknown request_type={request_type!r}")
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
        return q.filter(Candidate.email == req.subject_email).first()
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
    """Anonymize the candidate's direct PII and soft-delete the row."""
    for field in _ERASE_FIELDS:
        if hasattr(candidate, field):
            setattr(candidate, field, None)
    candidate.deleted_at = _utcnow()
    db.flush()


def fulfill_request(db: Session, org_id: int, req: DataSubjectRequest) -> Dict[str, Any]:
    """Execute a pending request. Access → returns the export payload; erasure →
    anonymizes the resolved candidate. Either way the request is marked
    completed. Returns a result dict (``export`` present for access requests)."""
    if req.status != DSR_STATUS_PENDING:
        raise HTTPException(status_code=409, detail="Request is not pending")
    candidate = _resolve_candidate(db, org_id, req)

    result: Dict[str, Any] = {"request_type": req.request_type}
    if req.request_type == DSR_TYPE_ACCESS:
        if candidate is None:
            raise HTTPException(status_code=404, detail="No candidate matched the request")
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


def reject_request(db: Session, req: DataSubjectRequest, *, reason: str) -> DataSubjectRequest:
    if req.status != DSR_STATUS_PENDING:
        raise HTTPException(status_code=409, detail="Request is not pending")
    req.status = DSR_STATUS_REJECTED
    req.notes = reason
    req.completed_at = _utcnow()
    db.flush()
    return req
