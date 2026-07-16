"""Per-role recruiting-agent pause and resume controls."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...agent_runtime import budget_guard
from ...deps import get_current_user
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_PAUSED,
    ROLE_CHANGE_ACTION_AGENT_RESUMED,
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ...services.role_concurrency import (
    assert_role_version,
    bump_role_version,
    role_query_for_update,
)
from ...services.workspace_agent_control import workspace_agent_pause_state


router = APIRouter()
logger = logging.getLogger("taali.agentic.routes")


class RoleVersionCommand(BaseModel):
    expected_version: int = Field(ge=1)


# Reason stamped on a recruiter-initiated manual pause. Distinct from the
# orchestrator's budget reasons so the activity tick / panel copy reads as a
# deliberate pause rather than "monthly budget reached".
MANUAL_PAUSE_REASON = "paused by recruiter"


def _compensate_failed_agent_dispatch(
    db: Session,
    *,
    role_id: int,
    dispatched_version: int,
    current_user: User,
) -> None:
    """Pause a failed resume without overwriting a later recruiter action."""

    role = (
        role_query_for_update(
            db,
            role_id=role_id,
            organization_id=int(current_user.organization_id),
        )
        .populate_existing()
        .first()
    )
    # The dispatch result belongs to the state that was just resumed. If a
    # recruiter deleted, disabled, or independently paused the role after that
    # commit, their newer control is already the safe terminal state.
    if (
        role is None
        or int(role.version or 1) != int(dispatched_version)
        or not bool(role.agentic_mode_enabled)
        or role.agent_paused_at is not None
    ):
        db.commit()
        return

    compensation_before = capture_role_change_snapshot(role)
    compensation_from = int(role.version or 1)
    budget_guard.pause_role(db, role=role, reason="agent bootstrap dispatch failed")
    role.agent_bootstrap_status = "failed"
    role.agent_bootstrap_error = "agent bootstrap dispatch failed"
    role.agent_bootstrap_completed_at = datetime.now(timezone.utc)
    compensation_to = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=compensation_before,
        action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
        actor_user_id=int(current_user.id),
        from_version=compensation_from,
        to_version=compensation_to,
        reason="agent bootstrap dispatch failed",
        request_id=get_request_id(),
    )
    db.commit()


class RoleAgentPauseResult(BaseModel):
    """Outcome of a per-role manual pause / resume."""

    role_id: int
    version: int
    paused: bool  # is the role paused after this call?
    pause_scope: Optional[Literal["workspace", "role"]] = None
    resumed: bool = False  # did this call actually clear a pause?
    reason: Optional[str] = None


@router.post("/roles/{role_id}/agent/pause", response_model=RoleAgentPauseResult)
def pause_role_agent(
    role_id: int,
    body: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually soft-pause ONE role's agent — the per-role twin of pause-all.

    Sets ``agent_paused_at`` (the flag the cohort sweeps honour, so the agent
    stops scoring/spending on the next beat) while leaving
    ``agentic_mode_enabled`` true. Crucially this KEEPS the role's pending
    decisions, and ``resume`` brings it straight back. Distinct from turning
    the agent off (PATCH ``agentic_mode_enabled=false``), which stops the agent
    indefinitely and doesn't auto-resume — neither path discards the queue.
    Idempotent: pausing an already-paused role is a no-op.
    """
    from ...services.agent_control_ats_fence import (
        require_authorized_agent_control_transaction_fence,
    )

    require_authorized_agent_control_transaction_fence(
        db,
        current_user=current_user,
        role_id=role_id,
    )
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")
    assert_role_version(
        role,
        expected_version=body.expected_version,
        current_role=lambda: {
            "id": int(role.id),
            "version": int(role.version or 1),
            "agentic_mode_enabled": bool(role.agentic_mode_enabled),
            "agent_paused_at": role.agent_paused_at.isoformat()
            if role.agent_paused_at
            else None,
            "agent_paused_reason": role.agent_paused_reason,
        },
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    if not bool(role.agentic_mode_enabled):
        raise HTTPException(
            status_code=409, detail="agent is not enabled for this role"
        )
    if role.agent_paused_at is None:
        audit_before = capture_role_change_snapshot(role)
        audit_from = int(role.version or 1)
        budget_guard.pause_role(db, role=role, reason=MANUAL_PAUSE_REASON)
        audit_to = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
            actor_user_id=int(current_user.id),
            from_version=audit_from,
            to_version=audit_to,
            reason=MANUAL_PAUSE_REASON,
            request_id=get_request_id(),
        )
        db.commit()
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return RoleAgentPauseResult(
        role_id=role_id,
        version=int(role.version or 1),
        paused=(
            bool(role.agentic_mode_enabled)
            and (bool(workspace_pause["paused"]) or role.agent_paused_at is not None)
        ),
        pause_scope=(
            "workspace"
            if workspace_pause["paused"]
            else ("role" if role.agent_paused_at is not None else None)
        ),
        reason=(
            workspace_pause["reason"]
            if workspace_pause["paused"]
            else role.agent_paused_reason
        ),
    )


@router.post("/roles/{role_id}/agent/resume", response_model=RoleAgentPauseResult)
def resume_role_agent(
    role_id: int,
    body: RoleVersionCommand,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume ONE paused role, if it's back under its monthly cap.

    Reuses ``budget_guard.resume_if_under_budget`` (the same guard as
    resume-all and the cap-raise auto-resume) so a genuinely over-budget role
    stays paused rather than resuming only to re-pause next cycle. On a real
    resume we kick an immediate review cycle so the recruiter doesn't wait up
    to 60 minutes for the next beat — mirroring the PATCH resume path.
    """
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .with_for_update(of=Role)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")
    assert_role_version(
        role,
        expected_version=body.expected_version,
        current_role=lambda: {
            "id": int(role.id),
            "version": int(role.version or 1),
            "agentic_mode_enabled": bool(role.agentic_mode_enabled),
            "agent_paused_at": role.agent_paused_at.isoformat()
            if role.agent_paused_at
            else None,
            "agent_paused_reason": role.agent_paused_reason,
        },
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    # Surface a concrete production-runtime failure on the explicit endpoint.
    # The shared budget_guard repeats this check at the mutation boundary so
    # non-HTTP resume paths fail closed too; this preflight exists to return an
    # actionable 503 instead of a misleading ``resumed=false`` when the budget
    # itself is already clear.
    if (
        bool(role.agentic_mode_enabled)
        and role.agent_paused_at is not None
        and budget_guard.check_monthly_usd(db, role=role).ok
    ):
        from ...services.agent_activation_readiness import (
            activation_readiness,
            readiness_message,
        )

        readiness = activation_readiness(role)
        if not readiness.get("ready"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Agent runtime is not ready: "
                    f"{readiness_message(readiness)}. The role remains paused."
                ),
            )
    audit_before = capture_role_change_snapshot(role)
    audit_from = int(role.version or 1)
    resumed = budget_guard.resume_if_under_budget(
        db,
        role=role,
        explicit=True,
    )
    if resumed:
        audit_to = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=audit_before,
            action=ROLE_CHANGE_ACTION_AGENT_RESUMED,
            actor_user_id=int(current_user.id),
            from_version=audit_from,
            to_version=audit_to,
            reason="resume requested by recruiter",
            request_id=get_request_id(),
        )
        db.commit()
        from ...services.workspace_agent_control import (
            workspace_agent_control_snapshot,
        )

        workspace_held, _workspace_control_version = workspace_agent_control_snapshot(
            db,
            organization_id=int(current_user.organization_id),
        )
        if not workspace_held:
            try:
                from ...services.role_agent_dispatch import dispatch_role_agent_cycle

                dispatch_role_agent_cycle(role, role_version=int(audit_to))
            except Exception:
                logger.exception(
                    "Failed to enqueue resume cycle for role_id=%s", role.id
                )
                _compensate_failed_agent_dispatch(
                    db,
                    role_id=int(role.id),
                    dispatched_version=int(audit_to),
                    current_user=current_user,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The agent worker queue is unavailable. The role was left "
                        "paused; retry Resume."
                    ),
                )
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    return RoleAgentPauseResult(
        role_id=role_id,
        version=int(role.version or 1),
        paused=(
            bool(role.agentic_mode_enabled)
            and (bool(workspace_pause["paused"]) or role.agent_paused_at is not None)
        ),
        pause_scope=(
            "workspace"
            if workspace_pause["paused"]
            else ("role" if role.agent_paused_at is not None else None)
        ),
        resumed=resumed,
        reason=(
            workspace_pause["reason"]
            if workspace_pause["paused"]
            else role.agent_paused_reason
        ),
    )
