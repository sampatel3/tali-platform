"""Per-job hiring-team management API (P0.5).

Assign who is on a specific job's hiring team and in what per-job role. Reads
and writes are open to any authenticated org member. Not wired into per-job
authorization enforcement yet — that is later work.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from .job_hiring_team_service import list_team, remove_member, set_member

router = APIRouter(tags=["Hiring Team"])


class HiringTeamMemberIn(BaseModel):
    user_id: int
    team_role: str = "interviewer"


class HiringTeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    team_role: str
    email: str | None = None
    name: str | None = None


def _serialize(row) -> HiringTeamMemberOut:
    user = getattr(row, "user", None)
    return HiringTeamMemberOut(
        user_id=row.user_id,
        team_role=row.team_role,
        email=getattr(user, "email", None),
        name=getattr(user, "full_name", None) or getattr(user, "name", None),
    )


@router.get("/roles/{role_id}/hiring-team", response_model=list[HiringTeamMemberOut])
def get_hiring_team(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [
        _serialize(row)
        for row in list_team(db, current_user.organization_id, role_id)
    ]


@router.post("/roles/{role_id}/hiring-team", response_model=HiringTeamMemberOut, status_code=201)
def add_hiring_team_member(
    role_id: int,
    data: HiringTeamMemberIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = set_member(
        db, current_user.organization_id, role_id, data.user_id, data.team_role
    )
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.delete("/roles/{role_id}/hiring-team/{user_id}", status_code=204)
def delete_hiring_team_member(
    role_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    remove_member(db, current_user.organization_id, role_id, user_id)
    db.commit()
    return None
