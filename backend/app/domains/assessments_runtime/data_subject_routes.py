"""P5: data-subject request API (GDPR access / erasure). Admin-only — these
operations expose or destroy personal data."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import require_role
from ...models.user import ROLE_ADMIN, User
from ...platform.database import get_db
from .data_subject_service import (
    create_request,
    fulfill_request,
    get_request,
    list_requests,
    reject_request,
)

_admin = require_role(ROLE_ADMIN)

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
    current_user: User = Depends(_admin),
):
    return list_requests(db, current_user.organization_id)


@router.post("/data-requests", response_model=RequestOut, status_code=201)
def post_data_request(
    data: RequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    req = create_request(
        db, current_user.organization_id,
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
    current_user: User = Depends(_admin),
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
    current_user: User = Depends(_admin),
):
    req = get_request(db, current_user.organization_id, request_id)
    reject_request(db, req, reason=body.reason)
    db.commit()
    db.refresh(req)
    return req
