"""Per-job hiring-team management (P0.5).

CRUD over ``job_hiring_team`` — who is on a specific job's hiring team and in
what per-job role (hiring_manager / recruiter / interviewer / coordinator). All
operations are org-scoped: the role AND the member user must belong to the
caller's org. ``is_hiring_team_member`` is the per-job authz primitive callers
can opt into (admins + org membership stay broad for now).

Mutators flush but do not commit — the caller owns the transaction.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.job_hiring_team import TEAM_ROLES, JobHiringTeam
from ...models.role import Role
from ...models.user import User


def _role_in_org(db: Session, organization_id: int, role_id: int) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def list_team(db: Session, organization_id: int, role_id: int) -> list[JobHiringTeam]:
    """The role's hiring-team memberships (org-scoped, stable order)."""
    _role_in_org(db, organization_id, role_id)
    return (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.organization_id == organization_id,
            JobHiringTeam.role_id == role_id,
        )
        .order_by(JobHiringTeam.id)
        .all()
    )


def set_member(
    db: Session, organization_id: int, role_id: int, user_id: int, team_role: str
) -> JobHiringTeam:
    """Add ``user_id`` to the role's hiring team (or update their team role if
    already on it). Both the role and the user must be in the caller's org."""
    if team_role not in TEAM_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid team_role {team_role!r}; expected one of {sorted(TEAM_ROLES)}",
        )
    _role_in_org(db, organization_id, role_id)
    member = (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == organization_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="User not found in this organization")

    row = (
        db.query(JobHiringTeam)
        .filter(JobHiringTeam.role_id == role_id, JobHiringTeam.user_id == user_id)
        .first()
    )
    if row is None:
        row = JobHiringTeam(
            organization_id=organization_id,
            role_id=role_id,
            user_id=user_id,
            team_role=team_role,
        )
        db.add(row)
    else:
        row.team_role = team_role
    db.flush()
    return row


def remove_member(
    db: Session, organization_id: int, role_id: int, user_id: int
) -> bool:
    """Remove a member from the role's hiring team. Returns False if they
    weren't on it."""
    _role_in_org(db, organization_id, role_id)
    row = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.organization_id == organization_id,
            JobHiringTeam.role_id == role_id,
            JobHiringTeam.user_id == user_id,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def is_hiring_team_member(db: Session, role_id: int, user_id: int) -> bool:
    """Per-job authz primitive: is this user on the job's hiring team? Callers
    wanting strict per-job access gate on ``admin or is_hiring_team_member``."""
    return (
        db.query(JobHiringTeam.id)
        .filter(JobHiringTeam.role_id == role_id, JobHiringTeam.user_id == user_id)
        .first()
        is not None
    )
