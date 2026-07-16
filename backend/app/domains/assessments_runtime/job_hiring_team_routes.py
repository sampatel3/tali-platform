"""Per-job hiring-team management API.

All organization members may see the team. Workspace owners make the initial
assignment; after that, hiring managers administer membership and job roles.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.role_concurrency import assert_role_version, bump_role_version
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
)
from .job_hiring_team_service import list_team, remove_member, set_member
from .job_authorization import JobPermission, require_job_permission

router = APIRouter(tags=["Hiring Team"])


class HiringTeamMemberIn(BaseModel):
    user_id: int
    team_role: str = "interviewer"
    expected_version: int = Field(ge=1)


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


def _audit_team_change(db: Session, *, role, current_user: User, reason: str) -> None:
    before = capture_role_change_snapshot(role)
    from_version = int(role.version or 1)
    to_version = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=before,
        action="hiring_team_updated",
        actor_user_id=int(current_user.id),
        from_version=from_version,
        to_version=to_version,
        reason=reason,
        request_id=get_request_id(),
        allow_empty_changes=True,
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
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.MANAGE_HIRING_TEAM,
    )
    assert_role_version(role, expected_version=data.expected_version)
    row = set_member(
        db, current_user.organization_id, role_id, data.user_id, data.team_role
    )
    _audit_team_change(
        db,
        role=role,
        current_user=current_user,
        reason=f"user {data.user_id} assigned as {data.team_role}",
    )
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.delete("/roles/{role_id}/hiring-team/{user_id}", status_code=204)
def delete_hiring_team_member(
    role_id: int,
    user_id: int,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.MANAGE_HIRING_TEAM,
    )
    assert_role_version(role, expected_version=expected_version)
    removed = remove_member(db, current_user.organization_id, role_id, user_id)
    if removed:
        _audit_team_change(
            db,
            role=role,
            current_user=current_user,
            reason=f"user {user_id} removed from hiring team",
        )
    db.commit()
    return None
