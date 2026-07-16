"""Workspace-wide agent pause state and execution authority.

The workspace switch is an overlay, not a bulk edit of role controls.  Role
``agentic_mode_enabled`` and ``agent_paused_*`` remain the recruiter's desired
state for that role; a workspace pause temporarily denies autonomous work for
all of them and clearing it reveals those local states unchanged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..models.organization import Organization
from ..models.user import User
from ..models.workspace_agent_control_event import WorkspaceAgentControlEvent


WORKSPACE_MANUAL_PAUSE_REASON = "workspace paused by recruiter"


def workspace_agent_control_snapshot(
    db: Session,
    *,
    organization_id: int,
    lock: bool = False,
) -> tuple[bool, int]:
    """Return ``(paused, version)`` for autonomous execution admission.

    ``lock=True`` linearizes a paid call or side-effect boundary with the
    workspace Pause/Resume endpoints.  Callers that also lock a role must take
    this organization lock first (organization -> role is the platform-wide
    provider-admission lock order).  A missing workspace fails open only for
    the overlay itself; the role/org ownership checks at the caller still fail
    closed in their normal way.
    """

    query = db.query(
        Organization.agent_workspace_paused_at,
        Organization.agent_workspace_control_version,
    ).filter(Organization.id == int(organization_id))
    if lock:
        query = query.with_for_update(of=Organization)
    row = query.one_or_none()
    if row is None:
        return False, 1
    return row.agent_workspace_paused_at is not None, int(
        row.agent_workspace_control_version or 1
    )


def workspace_agent_pause_state(
    db: Session,
    *,
    organization_id: int,
    current_user_id: int | None = None,
) -> dict[str, Any]:
    """Return the current workspace overlay with durable actor provenance."""

    row = (
        db.query(Organization, User)
        .outerjoin(
            User,
            and_(
                Organization.agent_workspace_paused_by_user_id == User.id,
                User.organization_id == Organization.id,
            ),
        )
        .filter(Organization.id == int(organization_id))
        .one_or_none()
    )
    if row is None:
        return {
            "paused": False,
            "paused_at": None,
            "reason": None,
            "paused_by": None,
            "version": 1,
            "last_change": None,
        }

    organization, actor = row
    paused_at = organization.agent_workspace_paused_at
    paused = paused_at is not None
    paused_by = None
    if paused:
        actor_user_id = (
            int(organization.agent_workspace_paused_by_user_id)
            if organization.agent_workspace_paused_by_user_id is not None
            else None
        )
        # Prefer the current account name, but retain the bounded snapshot when
        # the account has since been removed/anonymized.
        actor_name = (
            str(actor.full_name)[:200]
            if actor is not None and actor.full_name
            else organization.agent_workspace_paused_by_name
        )
        paused_by = {
            "user_id": actor_user_id,
            "name": actor_name,
            "is_current_user": (
                actor_user_id is not None
                and current_user_id is not None
                and actor_user_id == int(current_user_id)
            ),
            "changed_at": paused_at,
            "attribution": (
                "verified"
                if actor_user_id is not None
                else "unavailable"
            ),
            "source": "workspace_control",
        }

    latest_row = (
        db.query(WorkspaceAgentControlEvent, User)
        .outerjoin(
            User,
            and_(
                WorkspaceAgentControlEvent.actor_user_id == User.id,
                User.organization_id
                == WorkspaceAgentControlEvent.organization_id,
            ),
        )
        .filter(
            WorkspaceAgentControlEvent.organization_id == int(organization_id)
        )
        .order_by(WorkspaceAgentControlEvent.id.desc())
        .first()
    )
    last_change = None
    if latest_row is not None:
        event, event_actor = latest_row
        event_actor_id = (
            int(event.actor_user_id) if event.actor_user_id is not None else None
        )
        last_change = {
            "action": str(event.action),
            "user_id": event_actor_id,
            "name": (
                str(event_actor.full_name)[:200]
                if event_actor is not None and event_actor.full_name
                else event.actor_name
            ),
            "is_current_user": (
                event_actor_id is not None
                and current_user_id is not None
                and event_actor_id == int(current_user_id)
            ),
            "changed_at": event.created_at,
            "attribution": (
                "verified" if event_actor_id is not None else "unavailable"
            ),
            "source": "workspace_control",
        }

    return {
        "paused": paused,
        "paused_at": paused_at,
        "reason": organization.agent_workspace_paused_reason,
        "paused_by": paused_by,
        "version": int(organization.agent_workspace_control_version or 1),
        "last_change": last_change,
    }


def workspace_agent_is_paused(db: Session, *, organization_id: int) -> bool:
    """Cheap admission check used at autonomous paid/side-effect boundaries."""

    paused, _version = workspace_agent_control_snapshot(
        db,
        organization_id=organization_id,
    )
    return paused


__all__ = [
    "WORKSPACE_MANUAL_PAUSE_REASON",
    "workspace_agent_control_snapshot",
    "workspace_agent_is_paused",
    "workspace_agent_pause_state",
]
