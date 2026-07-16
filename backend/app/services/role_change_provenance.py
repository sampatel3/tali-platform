"""Bounded actor provenance queries for role-change audit records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..models.role_change_event import RoleChangeEvent
from ..models.user import User


def latest_role_change_actor(
    session: Session,
    organization_id: int,
    role_id: int,
    *,
    action: str | None = None,
) -> dict[str, Any] | None:
    """Describe the actor on the latest tenant-scoped role change.

    ``action`` narrows the lookup to one audit action when the caller needs
    provenance for a specific piece of current state. The outer join keeps
    conflict responses useful after an actor account is deleted.
    """

    query = (
        session.query(RoleChangeEvent, User)
        .outerjoin(
            User,
            and_(
                RoleChangeEvent.actor_user_id == User.id,
                User.organization_id == RoleChangeEvent.organization_id,
            ),
        )
        .filter(
            RoleChangeEvent.organization_id == int(organization_id),
            RoleChangeEvent.role_id == int(role_id),
        )
    )
    if action is not None:
        normalized_action = str(action).strip()
        if not normalized_action:
            return None
        query = query.filter(RoleChangeEvent.action == normalized_action)
    row = query.order_by(RoleChangeEvent.id.desc()).first()
    if row is None:
        return None
    event, user = row
    return {
        "user_id": int(user.id) if user is not None else None,
        "name": (
            str(user.full_name)[:200]
            if user is not None and user.full_name is not None
            else None
        ),
        "email": (
            str(user.email)[:320]
            if user is not None and user.email is not None
            else None
        ),
        "changed_at": (
            event.created_at.isoformat() if event.created_at is not None else None
        ),
    }


def infer_legacy_unique_org_actor(
    session: Session,
    *,
    organization_id: int,
    changed_at: datetime,
) -> dict[str, Any] | None:
    """Return the sole surviving org member who predates a legacy change.

    The result is inferred provenance only. It must never be used for
    authorization, conflict ownership, or append-only audit history.
    """

    users = (
        session.query(User)
        .filter(
            User.organization_id == int(organization_id),
            User.created_at <= changed_at,
        )
        .order_by(User.id.asc())
        .limit(2)
        .all()
    )
    if len(users) != 1:
        return None
    user = users[0]
    return {
        "user_id": int(user.id),
        "name": str(user.full_name)[:200] if user.full_name is not None else None,
        "changed_at": changed_at,
    }


__all__ = ["infer_legacy_unique_org_actor", "latest_role_change_actor"]
