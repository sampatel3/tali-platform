"""Workspace-wide pause overlay controls for recruiting agents."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...agent_runtime import budget_guard
from ...deps import require_org_owner
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...models.workspace_agent_control_event import WorkspaceAgentControlEvent
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.workspace_agent_control import (
    WORKSPACE_MANUAL_PAUSE_REASON,
    workspace_agent_pause_state,
)
from .status_routes import AgentStatusPausedBy


router = APIRouter()
logger = logging.getLogger("taali.agentic.routes")


class BulkAgentPauseResult(BaseModel):
    """Outcome of a workspace pause-overlay transition."""

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
    """Apply the workspace pause overlay without rewriting role controls.

    Role-level run/pause/off choices and review queues remain untouched. A
    workspace resume therefore restores the exact per-role desired state that
    existed underneath this hold instead of accidentally resuming roles that a
    recruiter or runtime guard had paused independently.
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
    # Same-target retries are idempotent even when their expected version is
    # stale. Preserve the original actor/time instead of letting a retry claim
    # someone else's already-current pause.
    if not current_state["paused"] and int(body.expected_control_version) != int(
        current_state["version"]
    ):
        raise _workspace_control_conflict(current_state)
    enabled_count = (
        db.query(Role.id)
        .filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .count()
    )
    locally_running_count = (
        db.query(Role.id)
        .filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.is_(None),
        )
        .count()
    )
    affected = 0
    if organization.agent_workspace_paused_at is None:
        now = datetime.now(timezone.utc)
        from_version = int(organization.agent_workspace_control_version or 1)
        to_version = from_version + 1
        organization.agent_workspace_paused_at = now
        organization.agent_workspace_paused_reason = WORKSPACE_MANUAL_PAUSE_REASON
        organization.agent_workspace_paused_by_user_id = int(current_user.id)
        organization.agent_workspace_paused_by_name = str(
            current_user.full_name or current_user.email
        )[:200]
        organization.agent_workspace_control_version = to_version
        db.add(
            WorkspaceAgentControlEvent(
                organization_id=int(current_user.organization_id),
                actor_user_id=int(current_user.id),
                actor_name=str(current_user.full_name or current_user.email)[:200],
                action="paused",
                from_version=from_version,
                to_version=to_version,
                reason=WORKSPACE_MANUAL_PAUSE_REASON,
                request_id=get_request_id(),
                created_at=now,
            )
        )
        affected = int(locally_running_count)
        db.commit()
    state = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return BulkAgentPauseResult(
        affected=affected,
        enabled_count=int(enabled_count),
        workspace_paused=bool(state["paused"]),
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
    """Clear only the workspace overlay and wake locally runnable roles.

    Local manual, budget and runtime pauses are deliberately not cleared. This
    is the critical distinction between workspace Resume and a role Resume.
    """
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
    # Resume retries after another caller already resumed are harmless. A stale
    # command that would actually clear a newer pause must be rejected.
    if current_state["paused"] and int(body.expected_control_version) != int(
        current_state["version"]
    ):
        raise _workspace_control_conflict(current_state)
    enabled_count = (
        db.query(Role.id)
        .filter(
            Role.organization_id == int(current_user.organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
        )
        .count()
    )
    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.is_(None),
        )
        .order_by(Role.id)
        .all()
    )
    was_paused = organization.agent_workspace_paused_at is not None
    workspace_resume_version = int(organization.agent_workspace_control_version or 1)
    if was_paused:
        now = datetime.now(timezone.utc)
        from_version = int(organization.agent_workspace_control_version or 1)
        to_version = from_version + 1
        organization.agent_workspace_paused_at = None
        organization.agent_workspace_paused_reason = None
        organization.agent_workspace_paused_by_user_id = None
        organization.agent_workspace_paused_by_name = None
        organization.agent_workspace_control_version = to_version
        workspace_resume_version = to_version
        db.add(
            WorkspaceAgentControlEvent(
                organization_id=int(current_user.organization_id),
                actor_user_id=int(current_user.id),
                actor_name=str(current_user.full_name or current_user.email)[:200],
                action="resumed",
                from_version=from_version,
                to_version=to_version,
                reason="workspace resumed by recruiter",
                request_id=get_request_id(),
                created_at=now,
            )
        )
        db.commit()

    dispatch_failed = 0
    if was_paused:
        from ...services.agent_activation_readiness import activation_readiness

    for role in roles if was_paused else []:
        if not budget_guard.check_monthly_usd(db, role=role).ok:
            dispatch_failed += 1
            continue
        if not activation_readiness(role).get("ready"):
            dispatch_failed += 1
            continue
        try:
            from ...services.role_agent_dispatch import dispatch_role_agent_cycle

            dispatch_role_agent_cycle(
                role, workspace_version=workspace_resume_version
            )
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
        affected=(len(roles) if was_paused else 0),
        enabled_count=int(enabled_count),
        skipped=dispatch_failed,
        workspace_paused=bool(state["paused"]),
        workspace_control_version=int(state["version"]),
        paused_at=state["paused_at"],
        paused_reason=state["reason"],
        paused_by=None,
    )
