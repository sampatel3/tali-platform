"""Compliance admin API — GDPR data-subject requests + aggregate EEO report.

Org-owner-gated (``require_org_owner``): these operations expose or destroy
personal data, or read the segregated EEO aggregate. The EEO report is
aggregate-only (never per-candidate) and small-cell-suppressed before it leaves
the org.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import require_org_owner
from ...models.user import User
from ...platform.database import get_db
from .data_subject_service import (
    create_request,
    fulfill_request,
    get_request,
    list_requests,
    reject_request,
)
from .eeo_service import aggregate_report, suppress_small_cells

router = APIRouter(prefix="/compliance", tags=["Compliance"])


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_id: int | None = None
    subject_email: str | None = None
    request_type: str
    status: str
    notes: str | None = None
    completed_at: datetime | None = None


class RequestCreate(BaseModel):
    request_type: str
    subject_email: str | None = None
    candidate_id: int | None = None
    notes: str | None = None


class RejectBody(BaseModel):
    reason: str


@router.get("/data-requests", response_model=list[RequestOut])
def get_data_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    return list_requests(db, current_user.organization_id)


@router.post("/data-requests", response_model=RequestOut, status_code=201)
def post_data_request(
    data: RequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    req = create_request(
        db,
        current_user.organization_id,
        request_type=data.request_type,
        subject_email=data.subject_email,
        candidate_id=data.candidate_id,
        requested_by_user_id=current_user.id,
        notes=data.notes,
    )
    db.commit()
    db.refresh(req)
    return req


@router.post("/data-requests/{request_id}/fulfill")
def post_fulfill_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    req = get_request(db, current_user.organization_id, request_id)
    result = fulfill_request(db, current_user.organization_id, req)
    db.commit()
    return result


@router.post("/data-requests/{request_id}/reject", response_model=RequestOut)
def post_reject_request(
    request_id: int,
    body: RejectBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    req = get_request(db, current_user.organization_id, request_id)
    reject_request(db, req, reason=body.reason)
    db.commit()
    db.refresh(req)
    return req


@router.get("/eeo-report")
def get_eeo_report(
    role_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Aggregate EEO self-ID counts (never per-candidate), small-cell-suppressed
    (cells below the k-anonymity threshold render as ``"<5"``). Owner-only."""
    raw = aggregate_report(db, current_user.organization_id, role_id=role_id)
    return suppress_small_cells(raw)
