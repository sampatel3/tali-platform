"""Requisition API (recruiter, JWT) — drive the AI-native intake.

Create a requisition, run the intake agent over pasted notes / a transcript / a
JD (it fills the full hiring brief), review/edit, then publish (materialize to a
role). The no-login conversational hiring-manager surface is a separate public
router; this is the authed recruiter path that's testable immediately.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.requisition_intake_agent import run_intake_extraction
from ...services.role_brief_service import (
    create_brief,
    materialize_brief_to_role,
    submit_brief,
    update_brief_fields,
)

router = APIRouter(tags=["Requisitions"])


class BriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role_id: Optional[int] = None
    status: str
    source_kind: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    department: Optional[str] = None
    location_city: Optional[str] = None
    location_country: Optional[str] = None
    workplace_type: Optional[str] = None
    employment_type: Optional[str] = None
    seniority: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    openings: Optional[int] = None
    target_start: Optional[str] = None
    must_haves: Optional[list] = None
    preferred: Optional[list] = None
    dealbreakers: Optional[list] = None
    success_profile: Optional[str] = None
    priorities: Optional[list] = None
    tradeoffs: Optional[list] = None
    calibration_exemplars: Optional[list] = None
    sourcing_signals: Optional[dict] = None
    assessment_focus: Optional[list] = None
    process: Optional[dict] = None
    evp: Optional[list] = None
    agent_state: Optional[dict] = None
    completeness: Optional[int] = None


class CreateRequisition(BaseModel):
    source_kind: Optional[str] = None


class IntakeInput(BaseModel):
    input: str
    source_kind: Optional[str] = None


def _get_brief(db: Session, organization_id: int, brief_id: int) -> RoleBrief:
    brief = (
        db.query(RoleBrief)
        .filter(RoleBrief.id == brief_id, RoleBrief.organization_id == organization_id)
        .first()
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return brief


@router.post("/requisitions", response_model=BriefOut, status_code=201)
def create_requisition(
    data: CreateRequisition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brief = create_brief(
        db,
        organization_id=current_user.organization_id,
        created_by_user_id=current_user.id,
        source_kind=data.source_kind,
    )
    db.commit()
    db.refresh(brief)
    return brief


@router.get("/requisitions", response_model=list[BriefOut])
def list_requisitions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == current_user.organization_id)
        .order_by(RoleBrief.id.desc())
        .all()
    )


@router.get("/requisitions/{brief_id}", response_model=BriefOut)
def get_requisition(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_brief(db, current_user.organization_id, brief_id)


@router.post("/requisitions/{brief_id}/intake", response_model=BriefOut)
def run_requisition_intake(
    brief_id: int,
    data: IntakeInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the intake agent over the input (notes / transcript / JD); it fills the
    brief. Calls Claude (metered)."""
    brief = _get_brief(db, current_user.organization_id, brief_id)
    result = run_intake_extraction(db, brief, data.input, source_kind=data.source_kind)
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status_code=502, detail=f"Intake extraction failed: {result.error_reason}"
        )
    db.commit()
    db.refresh(brief)
    return brief


@router.patch("/requisitions/{brief_id}", response_model=BriefOut)
def update_requisition(
    brief_id: int,
    data: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recruiter edits to the agent-drafted brief (whitelisted fields)."""
    brief = _get_brief(db, current_user.organization_id, brief_id)
    update_brief_fields(db, brief, **(data or {}))
    db.commit()
    db.refresh(brief)
    return brief


@router.post("/requisitions/{brief_id}/submit", response_model=BriefOut)
def submit_requisition(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brief = _get_brief(db, current_user.organization_id, brief_id)
    submit_brief(db, brief)
    db.commit()
    db.refresh(brief)
    return brief


@router.post("/requisitions/{brief_id}/publish")
def publish_requisition(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Materialize the brief onto a role (creates/updates the role)."""
    brief = _get_brief(db, current_user.organization_id, brief_id)
    role = materialize_brief_to_role(db, brief)
    db.commit()
    return {"role_id": role.id, "brief_id": brief.id, "status": brief.status}
