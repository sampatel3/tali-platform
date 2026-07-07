"""P3: interview scorecard CRUD + panel aggregation.

An interviewer owns one scorecard per (application, interview). Upsert is keyed
on that triple so re-submitting edits in place. Mutators flush but do NOT commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.interview_scorecard import (
    SCORECARD_NO,
    SCORECARD_RECOMMENDATIONS,
    SCORECARD_STRONG_NO,
    SCORECARD_STRONG_YES,
    SCORECARD_YES,
    InterviewScorecard,
)

# Numeric lean per recommendation (no_decision abstains → excluded from the mean).
_LEAN = {
    SCORECARD_STRONG_NO: -2,
    SCORECARD_NO: -1,
    SCORECARD_YES: 1,
    SCORECARD_STRONG_YES: 2,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _application_in_org(db: Session, org_id: int, application_id: int) -> CandidateApplication:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .first()
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


def list_scorecards(db: Session, org_id: int, application_id: int) -> List[InterviewScorecard]:
    _application_in_org(db, org_id, application_id)
    return (
        db.query(InterviewScorecard)
        .filter(
            InterviewScorecard.organization_id == org_id,
            InterviewScorecard.application_id == application_id,
        )
        .order_by(InterviewScorecard.id)
        .all()
    )


def get_scorecard(db: Session, org_id: int, scorecard_id: int) -> InterviewScorecard:
    card = (
        db.query(InterviewScorecard)
        .filter(
            InterviewScorecard.id == scorecard_id,
            InterviewScorecard.organization_id == org_id,
        )
        .first()
    )
    if card is None:
        raise HTTPException(status_code=404, detail="Scorecard not found")
    return card


def upsert_scorecard(
    db: Session,
    org_id: int,
    application_id: int,
    interviewer_user_id: int,
    *,
    interview_id: Optional[int] = None,
    recommendation: Optional[str] = None,
    overall_rating: Optional[int] = None,
    competencies: Optional[list] = None,
    notes: Optional[str] = None,
) -> InterviewScorecard:
    """Create or edit the caller's scorecard for (application, interview). The
    interviewer is always the caller — you can't file feedback under someone
    else's name."""
    _application_in_org(db, org_id, application_id)
    if recommendation is not None and recommendation not in SCORECARD_RECOMMENDATIONS:
        raise HTTPException(status_code=422, detail=f"Unknown recommendation={recommendation!r}")
    if overall_rating is not None and not (1 <= overall_rating <= 4):
        raise HTTPException(status_code=422, detail="overall_rating must be 1..4")

    card = (
        db.query(InterviewScorecard)
        .filter(
            InterviewScorecard.organization_id == org_id,
            InterviewScorecard.application_id == application_id,
            InterviewScorecard.interviewer_user_id == interviewer_user_id,
            InterviewScorecard.interview_id.is_(interview_id)
            if interview_id is None
            else InterviewScorecard.interview_id == interview_id,
        )
        .first()
    )
    if card is None:
        card = InterviewScorecard(
            organization_id=org_id,
            application_id=application_id,
            interview_id=interview_id,
            interviewer_user_id=interviewer_user_id,
        )
        db.add(card)
    if recommendation is not None:
        card.recommendation = recommendation
    if overall_rating is not None:
        card.overall_rating = overall_rating
    if competencies is not None:
        card.competencies = competencies
    if notes is not None:
        card.notes = notes
    db.flush()
    return card


def submit_scorecard(db: Session, card: InterviewScorecard) -> InterviewScorecard:
    if not card.recommendation:
        raise HTTPException(
            status_code=422, detail="A recommendation is required before submitting"
        )
    card.submitted_at = _utcnow()
    db.flush()
    return card


def delete_scorecard(db: Session, card: InterviewScorecard) -> None:
    db.delete(card)
    db.flush()


def panel_summary(db: Session, org_id: int, application_id: int) -> Dict[str, Any]:
    """Aggregate the SUBMITTED scorecards for an application: recommendation
    tally, mean lean, and mean overall rating. Drafts are excluded."""
    cards = [c for c in list_scorecards(db, org_id, application_id) if c.submitted_at]
    tally = {r: 0 for r in SCORECARD_RECOMMENDATIONS}
    leans: list[int] = []
    ratings: list[int] = []
    for c in cards:
        if c.recommendation in tally:
            tally[c.recommendation] += 1
        if c.recommendation in _LEAN:
            leans.append(_LEAN[c.recommendation])
        if isinstance(c.overall_rating, int):
            ratings.append(c.overall_rating)
    return {
        "submitted_count": len(cards),
        "recommendations": tally,
        "mean_lean": round(sum(leans) / len(leans), 2) if leans else None,
        "mean_overall_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
    }
