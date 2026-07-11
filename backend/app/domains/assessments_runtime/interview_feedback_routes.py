"""Structured interview-feedback CRUD for an application.

Recruiters record what happened in an interview (round, overall
recommendation, optional 5-Ds ratings, per-probe results, notes). These rows
are what the score↔outcome calibration script joins against to measure
predictive validity. No LLM calls — pure human-entered data.

Mounted under the ``/roles`` router assembly (same as applications_routes),
so paths resolve at ``/api/v1/applications/{id}/interview-feedback``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.interview_feedback import INTERVIEW_RECOMMENDATIONS, InterviewFeedback
from ...models.user import User
from ...platform.database import get_db
from .role_support import get_application

router = APIRouter(tags=["Roles"])

# result values for a single probe row, tying back to the interview kit.
PROBE_RESULTS = {"confirmed", "refuted", "not_probed"}
# the 5-Ds axes the optional dimension ratings are keyed by.
DIMENSION_AXES = {"delegation", "description", "discernment", "diligence", "deliverable"}


class ProbeResult(BaseModel):
    criterion_id: Optional[str] = None
    criterion_text: Optional[str] = None
    result: str = Field(...)


class InterviewFeedbackCreate(BaseModel):
    interviewer_name: Optional[str] = Field(default=None, max_length=200)
    interview_round: str = Field(default="interview", max_length=100)
    overall_recommendation: str
    dimension_ratings: Optional[dict[str, int]] = None
    probe_results: Optional[list[ProbeResult]] = None
    notes: Optional[str] = Field(default=None, max_length=10000)


class InterviewFeedbackUpdate(BaseModel):
    interviewer_name: Optional[str] = Field(default=None, max_length=200)
    interview_round: Optional[str] = Field(default=None, max_length=100)
    overall_recommendation: Optional[str] = None
    dimension_ratings: Optional[dict[str, int]] = None
    probe_results: Optional[list[ProbeResult]] = None
    notes: Optional[str] = Field(default=None, max_length=10000)


class InterviewFeedbackResponse(BaseModel):
    id: int
    application_id: int
    role_id: int
    interviewer_user_id: Optional[int] = None
    interviewer_name: Optional[str] = None
    interview_round: str
    overall_recommendation: str
    dimension_ratings: Optional[dict[str, Any]] = None
    probe_results: Optional[list[dict[str, Any]]] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def interview_feedback_to_dict(fb: InterviewFeedback) -> dict[str, Any]:
    """Serialize one row for the API and the detail payload (newest-first list)."""
    return {
        "id": fb.id,
        "application_id": fb.application_id,
        "role_id": fb.role_id,
        "interviewer_user_id": fb.interviewer_user_id,
        "interviewer_name": fb.interviewer_name,
        "interview_round": fb.interview_round,
        "overall_recommendation": fb.overall_recommendation,
        "dimension_ratings": fb.dimension_ratings,
        "probe_results": fb.probe_results,
        "notes": fb.notes,
        "created_at": fb.created_at,
        "updated_at": fb.updated_at,
    }


def _validate_recommendation(value: str) -> None:
    if value not in INTERVIEW_RECOMMENDATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"overall_recommendation must be one of {sorted(INTERVIEW_RECOMMENDATIONS)}",
        )


def _validate_dimension_ratings(ratings: Optional[dict[str, int]]) -> None:
    if ratings is None:
        return
    for axis, score in ratings.items():
        if axis not in DIMENSION_AXES:
            raise HTTPException(
                status_code=422,
                detail=f"unknown dimension axis '{axis}'; allowed: {sorted(DIMENSION_AXES)}",
            )
        if not isinstance(score, int) or not (1 <= score <= 5):
            raise HTTPException(
                status_code=422,
                detail=f"dimension rating for '{axis}' must be an integer 1–5",
            )


def _validate_probe_results(probes: Optional[list[ProbeResult]]) -> None:
    if probes is None:
        return
    for probe in probes:
        if probe.result not in PROBE_RESULTS:
            raise HTTPException(
                status_code=422,
                detail=f"probe result must be one of {sorted(PROBE_RESULTS)}",
            )


def _load_feedback(
    db: Session, *, feedback_id: int, org_id: int, application_id: int
) -> InterviewFeedback:
    fb = (
        db.query(InterviewFeedback)
        .filter(
            InterviewFeedback.id == feedback_id,
            InterviewFeedback.application_id == application_id,
            InterviewFeedback.organization_id == org_id,
        )
        .first()
    )
    if not fb:
        raise HTTPException(status_code=404, detail="Interview feedback not found")
    return fb


@router.post(
    "/applications/{application_id}/interview-feedback",
    response_model=InterviewFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_interview_feedback(
    application_id: int,
    data: InterviewFeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    _validate_recommendation(data.overall_recommendation)
    _validate_dimension_ratings(data.dimension_ratings)
    _validate_probe_results(data.probe_results)

    fb = InterviewFeedback(
        organization_id=current_user.organization_id,
        application_id=app.id,
        role_id=app.role_id,
        interviewer_user_id=current_user.id,
        interviewer_name=(data.interviewer_name or None),
        interview_round=(data.interview_round or "interview"),
        overall_recommendation=data.overall_recommendation,
        dimension_ratings=data.dimension_ratings,
        probe_results=[p.model_dump() for p in data.probe_results] if data.probe_results else None,
        notes=(data.notes or None),
    )
    db.add(fb)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save interview feedback")
    db.refresh(fb)
    return interview_feedback_to_dict(fb)


@router.get(
    "/applications/{application_id}/interview-feedback",
    response_model=list[InterviewFeedbackResponse],
)
def list_interview_feedback(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    rows = (
        db.query(InterviewFeedback)
        .filter(
            InterviewFeedback.application_id == app.id,
            InterviewFeedback.organization_id == current_user.organization_id,
        )
        .order_by(InterviewFeedback.created_at.desc(), InterviewFeedback.id.desc())
        .all()
    )
    return [interview_feedback_to_dict(fb) for fb in rows]


@router.patch(
    "/applications/{application_id}/interview-feedback/{feedback_id}",
    response_model=InterviewFeedbackResponse,
)
def update_interview_feedback(
    application_id: int,
    feedback_id: int,
    data: InterviewFeedbackUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    fb = _load_feedback(
        db, feedback_id=feedback_id, org_id=current_user.organization_id, application_id=app.id
    )
    updates = data.model_dump(exclude_unset=True)

    if "overall_recommendation" in updates and updates["overall_recommendation"] is not None:
        _validate_recommendation(updates["overall_recommendation"])
        fb.overall_recommendation = updates["overall_recommendation"]
    if "dimension_ratings" in updates:
        _validate_dimension_ratings(updates["dimension_ratings"])
        fb.dimension_ratings = updates["dimension_ratings"]
    if "probe_results" in updates:
        _validate_probe_results(data.probe_results)
        fb.probe_results = (
            [p.model_dump() for p in data.probe_results] if data.probe_results else None
        )
    if "interviewer_name" in updates:
        fb.interviewer_name = updates["interviewer_name"] or None
    if "interview_round" in updates and updates["interview_round"] is not None:
        fb.interview_round = updates["interview_round"]
    if "notes" in updates:
        fb.notes = updates["notes"] or None

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update interview feedback")
    db.refresh(fb)
    return interview_feedback_to_dict(fb)


@router.delete(
    "/applications/{application_id}/interview-feedback/{feedback_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_interview_feedback(
    application_id: int,
    feedback_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = get_application(application_id, current_user.organization_id, db)
    fb = _load_feedback(
        db, feedback_id=feedback_id, org_id=current_user.organization_id, application_id=app.id
    )
    db.delete(fb)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete interview feedback")
    return None
