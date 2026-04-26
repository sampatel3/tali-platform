"""HTTP surface for the cv_match_v3.0 module.

Two routers, both mounted under ``/api/v1`` from ``main.py``:

- ``admin_router`` — ``GET /admin/cv-match/traces`` returns the most recent
  telemetry rows for an admin to spot-check the pipeline. Superuser-only.

- ``override_router`` — ``POST /candidates/{candidate_id}/cv-match-override``
  captures a recruiter's disagreement with a recommendation. The audit row
  is keyed by ``application_id`` (resolved from candidate + active role) and
  the authenticated recruiter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import get_current_user
from ..models.candidate_application import CandidateApplication
from ..models.cv_match_override import CvMatchOverride
from ..models.user import User
from ..platform.database import get_db
from .schemas import Recommendation
from .telemetry import recent_traces

admin_router = APIRouter(prefix="/admin/cv-match", tags=["cv-match-admin"])
override_router = APIRouter(prefix="/candidates", tags=["cv-match-override"])


def _require_admin(user: User) -> None:
    """Reject non-superusers with 403. Superuser flag comes from FastAPI-Users."""
    if not getattr(user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )


# --------------------------------------------------------------------------- #
# Admin: traces                                                                #
# --------------------------------------------------------------------------- #


class TraceRow(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())

    trace_id: str
    cv_hash: str = ""
    jd_hash: str = ""
    prompt_version: str = ""
    model_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    retry_count: int = 0
    validation_failures: int = 0
    cache_hit: bool = False
    final_status: str = ""
    created_at: str = ""


@admin_router.get("/traces", response_model=list[TraceRow])
def list_traces(
    limit: int = Query(50, ge=1, le=500),
    user: User = Depends(get_current_user),
):
    """Recent CV match traces. Admin-only."""
    _require_admin(user)
    rows = recent_traces(limit=limit)
    return [TraceRow.model_validate(row) for row in rows]


# --------------------------------------------------------------------------- #
# Override capture                                                             #
# --------------------------------------------------------------------------- #


class OverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: int
    original_trace_id: str = ""
    original_recommendation: Recommendation | None = None
    override_recommendation: Recommendation
    original_score: float | None = Field(default=None, ge=0, le=100)
    recruiter_notes: str = ""


class OverrideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_id: int
    application_id: int
    recruiter_id: int | None
    original_recommendation: str | None
    override_recommendation: str
    original_score: float | None
    recruiter_notes: str
    created_at: datetime


@override_router.post(
    "/{candidate_id}/cv-match-override",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_override(
    candidate_id: int,
    payload: OverrideRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Record a recruiter override of an LLM-derived recommendation.

    The endpoint is permissive on the original_* fields — recruiters may not
    always paste the trace id or original recommendation. The stored row is
    the source of truth for downstream analysis.
    """
    application = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.id == payload.application_id)
        .one_or_none()
    )
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
    if application.candidate_id != candidate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="application_id does not belong to this candidate",
        )

    row = CvMatchOverride(
        application_id=payload.application_id,
        recruiter_id=user.id,
        original_trace_id=payload.original_trace_id or None,
        original_recommendation=(
            payload.original_recommendation.value
            if payload.original_recommendation is not None
            else None
        ),
        override_recommendation=payload.override_recommendation.value,
        original_score=payload.original_score,
        recruiter_notes=payload.recruiter_notes or "",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return OverrideResponse(
        override_id=row.id,
        application_id=row.application_id,
        recruiter_id=row.recruiter_id,
        original_recommendation=row.original_recommendation,
        override_recommendation=row.override_recommendation,
        original_score=row.original_score,
        recruiter_notes=row.recruiter_notes or "",
        created_at=row.created_at,
    )
