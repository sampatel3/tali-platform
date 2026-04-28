"""HTTP surface for the cv_match_v3.0 module.

Two routers, both mounted under ``/api/v1`` from ``main.py``:

- ``admin_router`` — ``GET /admin/cv-match/traces`` returns the most recent
  telemetry rows for an admin to spot-check the pipeline. Superuser-only.
  Also exposes ``GET /admin/cv-match/fairness`` (RALPH 4.4): per-segment
  selection rate, scoring rate, and impact ratio over a rolling window.

- ``override_router`` — ``POST /candidates/{candidate_id}/cv-match-override``
  captures a recruiter's disagreement with a recommendation. The audit row
  is keyed by ``application_id`` (resolved from candidate + active role) and
  the authenticated recruiter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import get_current_user
from ..models.candidate_application import CandidateApplication
from ..models.cv_match_override import CvMatchOverride
from ..models.user import User
from ..platform.database import get_db
from .fairness.impact_ratio import (
    AMBER_THRESHOLD,
    ApplicationOutcome,
    GREEN_THRESHOLD,
    SegmentRow,
    compute_impact_ratios,
)
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
# Admin: fairness dashboard (RALPH 4.4)                                        #
# --------------------------------------------------------------------------- #


class FairnessRow(BaseModel):
    """One per-segment row of the impact-ratio dashboard."""

    model_config = ConfigDict(extra="forbid")

    segment_key: str
    n_applications: int
    n_scored: int
    n_advanced: int
    selection_rate: float
    scoring_rate: float
    impact_ratio: float | None
    rag: str  # "green" | "amber" | "red"


class FairnessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_days: int
    green_threshold: float
    amber_threshold: float
    rows: list[FairnessRow]
    notes: list[str] = Field(default_factory=list)


@admin_router.get("/fairness", response_model=FairnessReport)
def fairness_dashboard(
    window_days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-segment impact-ratio dashboard.

    Reads recent ``CandidateApplication`` rows (the last ``window_days``)
    and rolls up selection rate / scoring rate / impact ratio per
    segment. Segment attribution depends on a separately-stored
    diversity self-id mapping; until that table is wired, this
    endpoint returns a single "all" row.
    """
    _require_admin(user)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    apps = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.created_at >= cutoff)
        .all()
    )

    outcomes = []
    notes: list[str] = []
    for app in apps:
        details = getattr(app, "cv_match_details", {}) or {}
        outcomes.append(
            ApplicationOutcome(
                application_id=int(app.id),
                recommendation=details.get("recommendation"),
                scoring_status=details.get("scoring_status", "ok"),
            )
        )
    # Segment mapping placeholder. Real implementation reads from a
    # diversity self-id table (out of scope for this module).
    segment_for_application: dict[int, str] = {o.application_id: "all" for o in outcomes}
    if not segment_for_application:
        notes.append(
            f"No applications in the last {window_days} days; nothing to report."
        )
    else:
        notes.append(
            "Segment attribution placeholder: returning a single 'all' row "
            "until diversity self-id integration lands. Replace "
            "``segment_for_application`` with real attribution data when ready."
        )

    rows = compute_impact_ratios(outcomes, segment_for_application)
    return FairnessReport(
        window_days=window_days,
        green_threshold=GREEN_THRESHOLD,
        amber_threshold=AMBER_THRESHOLD,
        rows=[FairnessRow(**row.__dict__) for row in rows],
        notes=notes,
    )


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
