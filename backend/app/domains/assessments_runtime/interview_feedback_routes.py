"""Structured interview-feedback CRUD for an application.

Recruiters record what happened in an interview (round, overall
recommendation, optional 5-Ds ratings, per-probe results, notes). These rows
are what the score↔outcome calibration script joins against to measure
predictive validity. No LLM calls — pure human-entered data.

Mounted under the ``/roles`` router assembly (same as applications_routes),
so paths resolve at ``/api/v1/applications/{id}/interview-feedback``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.interview_feedback import (
    INTERVIEW_RECOMMENDATIONS,
    NO_LEAN_RECOMMENDATIONS,
    InterviewFeedback,
)
from ...models.user import User
from ...platform.database import get_db
from .role_support import get_application

router = APIRouter(tags=["Roles"])

# result values for a single probe row, tying back to the interview kit.
PROBE_RESULTS = {"confirmed", "refuted", "not_probed"}
# the 5-Ds axes the optional dimension ratings are keyed by.
DIMENSION_AXES = {"delegation", "description", "discernment", "diligence", "deliverable"}
# Numeric lean per recommendation for the panel mean (abstentions excluded).
_LEAN = {"strong_no": -2, "no": -1, "neutral": 0, "yes": 1, "strong_yes": 2}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    overall_rating: Optional[int] = None
    competencies: Optional[list[dict[str, Any]]] = None
    probe_results: Optional[list[dict[str, Any]]] = None
    notes: Optional[str] = None
    interview_id: Optional[int] = None
    submitted_at: Optional[datetime] = None
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
        "overall_rating": fb.overall_rating,
        "competencies": fb.competencies,
        "probe_results": fb.probe_results,
        "notes": fb.notes,
        "interview_id": fb.interview_id,
        "submitted_at": fb.submitted_at,
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
        # This endpoint records a completed interview — the row is submitted on
        # create (legacy semantics). The scorecard lifecycle below is where a
        # draft (submitted_at NULL) is written and later submitted.
        submitted_at=_utcnow(),
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


# --------------------------------------------------------------------------
# Scorecard lifecycle — the same interview_feedback row, driven as a per-
# interviewer draft/submit card. An interviewer owns one card per
# (application, interviewer, interview); re-posting edits it in place. The
# panel summary and calibration read only SUBMITTED cards.
# --------------------------------------------------------------------------
class ScorecardUpsert(BaseModel):
    interview_id: Optional[int] = None
    overall_recommendation: Optional[str] = None
    overall_rating: Optional[int] = None
    competencies: Optional[list[dict[str, Any]]] = None
    notes: Optional[str] = Field(default=None, max_length=10000)


def _validate_overall_rating(value: Optional[int]) -> None:
    if value is None:
        return
    if not isinstance(value, int) or not (1 <= value <= 4):
        raise HTTPException(status_code=422, detail="overall_rating must be an integer 1–4")


def _own_card_or_404(
    db: Session, *, feedback_id: int, org_id: int, application_id: int, user_id: int
) -> InterviewFeedback:
    """Load the caller's OWN card. A row belonging to another interviewer 404s —
    you can only edit/submit feedback filed under your name."""
    fb = _load_feedback(
        db, feedback_id=feedback_id, org_id=org_id, application_id=application_id
    )
    if fb.interviewer_user_id != user_id:
        raise HTTPException(status_code=404, detail="Interview feedback not found")
    return fb


@router.get("/applications/{application_id}/scorecards/summary")
def get_scorecard_summary(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Panel summary over SUBMITTED cards: count, mean lean, mean overall
    rating, and the recommendation distribution. Drafts and abstentions are
    excluded from the mean lean."""
    app = get_application(application_id, current_user.organization_id, db)
    cards = (
        db.query(InterviewFeedback)
        .filter(
            InterviewFeedback.application_id == app.id,
            InterviewFeedback.organization_id == current_user.organization_id,
            InterviewFeedback.submitted_at.isnot(None),
        )
        .all()
    )
    tally = {rec: 0 for rec in INTERVIEW_RECOMMENDATIONS}
    leans: list[int] = []
    ratings: list[int] = []
    for c in cards:
        if c.overall_recommendation in tally:
            tally[c.overall_recommendation] += 1
        if (
            c.overall_recommendation not in NO_LEAN_RECOMMENDATIONS
            and c.overall_recommendation in _LEAN
        ):
            leans.append(_LEAN[c.overall_recommendation])
        if isinstance(c.overall_rating, int):
            ratings.append(c.overall_rating)
    return {
        "submitted_count": len(cards),
        "recommendations": tally,
        "mean_lean": round(sum(leans) / len(leans), 2) if leans else None,
        "mean_overall_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
    }


@router.post(
    "/applications/{application_id}/scorecards",
    response_model=InterviewFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
def upsert_scorecard(
    application_id: int,
    data: ScorecardUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or edit the caller's OWN card for (application, interview). Keyed
    on (application_id, interviewer_user_id, interview_id) so re-posting edits
    in place. Stays a draft (submitted_at NULL) until explicitly submitted."""
    app = get_application(application_id, current_user.organization_id, db)
    if data.overall_recommendation is not None:
        _validate_recommendation(data.overall_recommendation)
    _validate_overall_rating(data.overall_rating)

    interview_id_filter = (
        InterviewFeedback.interview_id.is_(None)
        if data.interview_id is None
        else InterviewFeedback.interview_id == data.interview_id
    )
    card = (
        db.query(InterviewFeedback)
        .filter(
            InterviewFeedback.organization_id == current_user.organization_id,
            InterviewFeedback.application_id == app.id,
            InterviewFeedback.interviewer_user_id == current_user.id,
            interview_id_filter,
        )
        .first()
    )
    if card is None:
        card = InterviewFeedback(
            organization_id=current_user.organization_id,
            application_id=app.id,
            role_id=app.role_id,
            interviewer_user_id=current_user.id,
            interview_id=data.interview_id,
            interview_round="interview",
            # NOT NULL on the model; a draft may not have a recommendation yet,
            # so seed the abstention until the interviewer picks one.
            overall_recommendation=data.overall_recommendation or "no_decision",
        )
        db.add(card)
    if data.overall_recommendation is not None:
        card.overall_recommendation = data.overall_recommendation
    if data.overall_rating is not None:
        card.overall_rating = data.overall_rating
    if data.competencies is not None:
        card.competencies = data.competencies
    if data.notes is not None:
        card.notes = data.notes or None

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save scorecard")
    db.refresh(card)
    return interview_feedback_to_dict(card)


@router.post(
    "/applications/{application_id}/scorecards/{feedback_id}/submit",
    response_model=InterviewFeedbackResponse,
)
def submit_scorecard(
    application_id: int,
    feedback_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit the caller's OWN card. Requires a real recommendation (an
    abstention can't stand in as a decision to submit)."""
    app = get_application(application_id, current_user.organization_id, db)
    card = _own_card_or_404(
        db,
        feedback_id=feedback_id,
        org_id=current_user.organization_id,
        application_id=app.id,
        user_id=current_user.id,
    )
    if not card.overall_recommendation or card.overall_recommendation in NO_LEAN_RECOMMENDATIONS:
        raise HTTPException(
            status_code=422, detail="A recommendation is required before submitting"
        )
    card.submitted_at = _utcnow()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit scorecard")
    db.refresh(card)
    return interview_feedback_to_dict(card)
