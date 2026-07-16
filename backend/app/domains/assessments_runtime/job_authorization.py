"""Authorization policy for shared, per-job mutations.

The organization boundary is checked before the permission matrix.  A missing
role and a role in another organization therefore produce the same response so
callers cannot use this helper to discover another tenant's role IDs.

Roles without hiring-team rows fail closed for ordinary members. Workspace
owners remain the break-glass administrators and make the first assignment;
the collaboration migration backfills existing live roles to their owners.
"""
from __future__ import annotations

from enum import Enum

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.job_hiring_team import (
    TEAM_ROLE_HIRING_MANAGER,
    TEAM_ROLE_RECRUITER,
    JobHiringTeam,
)
from ...models.role import Role
from ...models.user import User


class JobPermission(str, Enum):
    """Operations governed by the per-job hiring-team policy."""

    VIEW = "view"
    EDIT_ROLE = "edit_role"
    CONTROL_AGENT = "control_agent"
    MANAGE_HIRING_TEAM = "manage_hiring_team"
    DELETE_ROLE = "delete_role"


_TEAM_ROLE_PERMISSIONS: dict[JobPermission, frozenset[str]] = {
    JobPermission.EDIT_ROLE: frozenset(
        {TEAM_ROLE_HIRING_MANAGER, TEAM_ROLE_RECRUITER}
    ),
    JobPermission.CONTROL_AGENT: frozenset(
        {TEAM_ROLE_HIRING_MANAGER, TEAM_ROLE_RECRUITER}
    ),
    JobPermission.MANAGE_HIRING_TEAM: frozenset({TEAM_ROLE_HIRING_MANAGER}),
    JobPermission.DELETE_ROLE: frozenset({TEAM_ROLE_HIRING_MANAGER}),
}


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def has_job_permission_for_role(
    db: Session,
    *,
    current_user: User,
    role: Role,
    permission: JobPermission | str,
) -> bool:
    """Return the canonical viewer capability for an already-scoped role.

    Read responses use this helper to describe which controls the current
    viewer may use.  Mutations still call :func:`require_job_permission`; both
    paths therefore share one policy matrix and cannot drift into a UI-only
    grant.  The helper fails closed for inactive users, deleted/cross-tenant
    roles, unsupported membership, and unconfigured hiring teams.
    """
    try:
        requested_permission = JobPermission(permission)
    except ValueError as exc:
        raise ValueError(f"Unsupported job permission: {permission!r}") from exc

    organization_id = getattr(current_user, "organization_id", None)
    if (
        organization_id is None
        or not bool(getattr(current_user, "is_active", False))
        or getattr(role, "organization_id", None) != organization_id
        or getattr(role, "deleted_at", None) is not None
    ):
        return False

    if getattr(current_user, "role", None) == "owner":
        return True
    if requested_permission is JobPermission.VIEW:
        return True

    membership = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.organization_id == organization_id,
            JobHiringTeam.role_id == role.id,
            JobHiringTeam.user_id == current_user.id,
        )
        .first()
    )
    allowed_team_roles = _TEAM_ROLE_PERMISSIONS[requested_permission]
    return bool(
        membership is not None and membership.team_role in allowed_team_roles
    )


def require_job_permission(
    db: Session,
    *,
    current_user: User,
    role_id: int,
    permission: JobPermission | str,
    lock_for_update: bool = True,
) -> Role:
    """Return the tenant-scoped role when ``current_user`` may perform an action.

    Policy:

    * Organization owners may perform every operation on roles in their org.
    * Viewing remains open to all members of the role's organization.
    * Recruiters and hiring managers may edit a role or control its agent.
    * Only hiring managers (and organization owners) may manage a configured
      hiring team or permanently delete an empty job. Interviewers and
      coordinators have no mutation permissions.
    * An unconfigured role is owner-only until its first team assignment.

    Authorization failures, including missing/cross-org roles and users without
    an organization, consistently raise ``HTTP 403``. An unsupported permission
    is a caller programming error and raises ``ValueError``.
    """
    try:
        requested_permission = JobPermission(permission)
    except ValueError as exc:
        raise ValueError(f"Unsupported job permission: {permission!r}") from exc

    organization_id = getattr(current_user, "organization_id", None)
    if organization_id is None or not bool(getattr(current_user, "is_active", False)):
        raise _forbidden()

    role_query = db.query(Role).filter(
        Role.id == role_id,
        Role.organization_id == organization_id,
        Role.deleted_at.is_(None),
    )
    # Every mutation takes the shared role row lock before checking its team
    # membership. Hiring-team mutations use this same helper, so removing a
    # recruiter's access cannot race between authorization and the write.
    # Read-only previews may opt out while still applying the identical policy.
    if requested_permission is not JobPermission.VIEW and lock_for_update:
        role_query = role_query.with_for_update(of=Role)
    role = role_query.first()
    if role is None:
        raise _forbidden()

    if has_job_permission_for_role(
        db,
        current_user=current_user,
        role=role,
        permission=requested_permission,
    ):
        return role

    raise _forbidden()


__all__ = [
    "JobPermission",
    "has_job_permission_for_role",
    "require_job_permission",
]
