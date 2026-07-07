"""P3: interview scorecard API.

Reads (list + panel summary) open to any authed org member. Writes require an
authed user and file the scorecard under the caller — an interviewer edits and
submits their OWN feedback; admins may also remove any scorecard.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import get_current_user, require_role
from ...models.user import ROLE_ADMIN, User
from ...platform.database import get_db
from .scorecard_service import (
    delete_scorecard,
    get_scorecard,
    list_scorecards,
    panel_summary,
    submit_scorecard,
    upsert_scorecard,
)

_authed = require_role()  # any authenticated org member

router = APIRouter(tags=["Interview Scorecards"])


class ScorecardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    interview_id: int | None = None
    interviewer_user_id: int
    recommendation: str | None = None
    overall_rating: int | None = None
    competencies: list | None = None
    notes: str | None = None
    submitted_at: datetime | None = None


class ScorecardUpsert(BaseModel):
    interview_id: int | None = None
    recommendation: str | None = None
    overall_rating: int | None = None
    competencies: list | None = None
    notes: str | None = None


@router.get("/applications/{application_id}/scorecards", response_model=list[ScorecardOut])
def get_application_scorecards(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return list_scorecards(db, current_user.organization_id, application_id)


@router.get("/applications/{application_id}/scorecards/summary")
def get_application_scorecard_summary(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return panel_summary(db, current_user.organization_id, application_id)


@router.post(
    "/applications/{application_id}/scorecards",
    response_model=ScorecardOut,
    status_code=201,
)
def upsert_application_scorecard(
    application_id: int,
    data: ScorecardUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(_authed),
):
    card = upsert_scorecard(
        db,
        current_user.organization_id,
        application_id,
        current_user.id,
        **data.model_dump(),
    )
    db.commit()
    db.refresh(card)
    return card


def _owned_or_admin(card, current_user: User):
    if card.interviewer_user_id != current_user.id and current_user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Not your scorecard")


@router.post("/scorecards/{scorecard_id}/submit", response_model=ScorecardOut)
def post_submit_scorecard(
    scorecard_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_authed),
):
    card = get_scorecard(db, current_user.organization_id, scorecard_id)
    _owned_or_admin(card, current_user)
    submit_scorecard(db, card)
    db.commit()
    db.refresh(card)
    return card


@router.delete("/scorecards/{scorecard_id}", status_code=204)
def remove_scorecard(
    scorecard_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_authed),
):
    card = get_scorecard(db, current_user.organization_id, scorecard_id)
    _owned_or_admin(card, current_user)
    delete_scorecard(db, card)
    db.commit()
    return None
