"""Workspace-wide bulk role controls for recruiting agents."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...agent_runtime import budget_guard
from ...deps import require_org_owner
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_PAUSED,
    ROLE_CHANGE_ACTION_AGENT_RESUMED,
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import bump_role_version
from ...services.workspace_agent_control import (
    WORKSPACE_BULK_PAUSE_REASON,
    advance_workspace_control,
    workspace_agent_pause_state,
)
from .status_routes import AgentStatusPausedBy


router = APIRouter()
logger = logging.getLogger("taali.agentic.routes")


class BulkAgentPauseResult(BaseModel):
    """Outcome of a workspace bulk role-control transition."""

    affected: int  # enabled roles whose effective state changed this call
    enabled_count: int  # agent-enabled roles considered
    skipped: int = 0  # newly unblocked roles not immediately dispatched
    workspace_paused: bool
    workspace_control_version: int
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    paused_by: Optional[AgentStatusPausedBy] = None


class WorkspaceControlCommand(BaseModel):
    expected_control_version: int = Field(ge=1)


def _workspace_control_conflict(state: dict[str, Any]) -> HTTPException:
    paused_at = state["paused_at"]
    paused_by = state["paused_by"]
    last_change = state.get("last_change")
    return HTTPException(
        status_code=409,
        detail={
            "message": (
                "Workspace agent control changed after you opened this page. "
                "The latest state is shown; review it and try again."
            ),
            "current": {
                "workspace_paused": bool(state["paused"]),
                "workspace_control_version": int(state["version"]),
                "paused_at": (
                    paused_at.isoformat()
                    if isinstance(paused_at, datetime)
                    else paused_at
                ),
                "paused_reason": state["reason"],
                "paused_by": (
                    AgentStatusPausedBy(**paused_by).model_dump(mode="json")
                    if paused_by is not None
                    else None
                ),
                "changed_by": (
                    {
                        **last_change,
                        "changed_at": (
                            last_change["changed_at"].isoformat()
                            if isinstance(last_change.get("changed_at"), datetime)
                            else last_change.get("changed_at")
                        ),
                    }
                    if last_change is not None
                    else None
                ),
            },
        },
    )


@router.post("/agent/pause-all", response_model=BulkAgentPauseResult)
def pause_all_agents(
    body: WorkspaceControlCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Pause every currently-running role without creating a global blocker.

    The workspace control is a convenience bulk action. Roles already paused
    manually or by a runtime guard remain untouched, and any role paused here
    can be resumed independently from its own page.
    """
    organization = (
        db.query(Organization)
        .filter(
            Organization.id == int(current_user.organization_id),
        )
        .with_for_update(of=Organization)
        .one()
    )
    current_state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    last_action = (current_state.get("last_change") or {}).get("action")
    if int(body.expected_control_version) != int(current_state["version"]):
        enabled_roles = db.query(Role.id).filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None), Role.agentic_mode_enabled.is_(True),
        )
        enabled_count = enabled_roles.count()
        if last_action == "paused" and enabled_roles.filter(Role.agent_paused_at.is_(None)).count() == 0:
            return BulkAgentPauseResult(
                affected=0,
                enabled_count=enabled_count,
                workspace_paused=False,
                workspace_control_version=int(current_state["version"]),
            )
        raise _workspace_control_conflict(current_state)
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .order_by(Role.id)
        .with_for_update(of=Role)
        .all()
    )
    enabled_count = len(roles)
    selected = [role for role in roles if role.agent_paused_at is None]
    affected = len(selected)
    if selected or organization.agent_workspace_paused_at is not None:
        advance_workspace_control(
            db,
            organization=organization,
            actor_user_id=int(current_user.id),
            actor_name=str(current_user.full_name or current_user.email),
            action="paused",
            reason=WORKSPACE_BULK_PAUSE_REASON,
            request_id=get_request_id(),
        )
        for role in selected:
            before = capture_role_change_snapshot(role)
            role_from_version = int(role.version or 1)
            budget_guard.pause_role(
                db, role=role, reason=WORKSPACE_BULK_PAUSE_REASON
            )
            role_to_version = bump_role_version(role)
            add_role_change_event(
                db,
                role=role,
                before=before,
                action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
                actor_user_id=int(current_user.id),
                from_version=role_from_version,
                to_version=role_to_version,
                reason=WORKSPACE_BULK_PAUSE_REASON,
                request_id=get_request_id(),
            )
        db.commit()
    state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return BulkAgentPauseResult(
        affected=affected,
        enabled_count=int(enabled_count),
        # Deliberately false: there is no workspace execution overlay.
        workspace_paused=False,
        workspace_control_version=int(state["version"]),
        paused_at=state["paused_at"],
        paused_reason=state["reason"],
        paused_by=(
            AgentStatusPausedBy(**state["paused_by"])
            if state["paused_by"] is not None
            else None
        ),
    )


@router.post("/agent/resume-all", response_model=BulkAgentPauseResult)
def resume_all_agents(
    body: WorkspaceControlCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_owner),
):
    """Resume every paused enabled role whose safety checks are healthy."""
    organization = (
        db.query(Organization)
        .filter(Organization.id == int(current_user.organization_id))
        .with_for_update(of=Organization)
        .one()
    )
    current_state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    last_action = (current_state.get("last_change") or {}).get("action")
    if int(body.expected_control_version) != int(current_state["version"]):
        enabled_roles = db.query(Role.id).filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None), Role.agentic_mode_enabled.is_(True),
        )
        enabled_count = enabled_roles.count()
        if last_action == "resumed" and enabled_roles.filter(Role.agent_paused_at.isnot(None)).count() == 0:
            return BulkAgentPauseResult(
                affected=0,
                enabled_count=int(enabled_count),
                workspace_paused=False,
                workspace_control_version=int(current_state["version"]),
            )
        raise _workspace_control_conflict(current_state)
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.isnot(None),
        )
        .order_by(Role.id)
        .with_for_update(of=Role)
        .all()
    )
    had_legacy_overlay = organization.agent_workspace_paused_at is not None
    enabled_count = db.query(Role.id).filter(
        Role.organization_id == int(current_user.organization_id),
        Role.deleted_at.is_(None),
        Role.agentic_mode_enabled.is_(True),
    ).count()
    if roles or had_legacy_overlay:
        advance_workspace_control(
            db,
            organization=organization,
            actor_user_id=int(current_user.id),
            actor_name=str(current_user.full_name or current_user.email),
            action="resumed",
            reason="workspace resumed by recruiter",
            request_id=get_request_id(),
        )
    resumed_roles: list[tuple[Role, int]] = []
    skipped = 0
    for role in roles:
        before = capture_role_change_snapshot(role)
        role_from_version = int(role.version or 1)
        if not budget_guard.resume_if_under_budget(db, role=role, explicit=True):
            skipped += 1
            continue
        role_to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=before,
            action=ROLE_CHANGE_ACTION_AGENT_RESUMED,
            actor_user_id=int(current_user.id),
            from_version=role_from_version,
            to_version=role_to_version,
            reason="workspace resumed by recruiter",
            request_id=get_request_id(),
        )
        resumed_roles.append((role, role_to_version))
    if roles or had_legacy_overlay:
        db.commit()

    dispatch_failed = 0
    for role, role_version in resumed_roles:
        try:
            from ...services.role_agent_dispatch import dispatch_role_agent_cycle

            dispatch_role_agent_cycle(role, role_version=role_version)
        except Exception:
            logger.exception(
                "Failed to enqueue workspace-resume cycle for role_id=%s", role.id
            )
            dispatch_failed += 1

    state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return BulkAgentPauseResult(
        affected=len(resumed_roles),
        enabled_count=int(enabled_count),
        skipped=skipped + dispatch_failed,
        workspace_paused=False,
        workspace_control_version=int(state["version"]),
        paused_at=state["paused_at"],
        paused_reason=state["reason"],
        paused_by=None,
    )
