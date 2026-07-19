"""Read-only role agent status and activity endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from ...agent_runtime import budget_guard
from ...deps import get_current_user
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    has_job_permission_for_role,
)
from ...models.agent_decision import AgentDecision
from ...models.agent_needs_input import AgentNeedsInput
from ...models.agent_run import AgentRun
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ...services.role_change_audit import (
    ROLE_CHANGE_ACTION_AGENT_PAUSED,
    infer_legacy_unique_org_actor,
    latest_role_change_actor,
)
from ...services.workspace_agent_control import workspace_agent_pause_state
from ._activity_feed import AgentActivityPayload, build_activity_feed


router = APIRouter()


class AgentStatusActivity(BaseModel):
    event_type: str
    reason: Optional[str] = None
    actor_type: str
    application_id: Optional[int] = None
    candidate_name: Optional[str] = None
    created_at: datetime


class AgentStatusCurrentRun(BaseModel):
    id: int
    started_at: datetime
    status: str
    decisions_emitted: int
    tools_called: Optional[list[dict[str, Any]]] = None


class AgentStatusPausedBy(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    is_current_user: bool
    changed_at: Optional[datetime] = None
    attribution: Literal["verified", "inferred", "unavailable"]
    source: Literal[
        "role_change_event",
        "legacy_unique_member",
        "legacy_history",
        "workspace_control",
    ]


class AgentStatusPendingBreakdown(BaseModel):
    total: int
    decisions: int
    questions: int


class AgentStatusPayload(BaseModel):
    role_id: int
    enabled: bool
    # Viewer-specific capability from the same hiring-team policy enforced by
    # every role agent mutation. Clients use it only to render controls as
    # read-only; the mutation endpoints remain the authority.
    can_control_agent: bool = False
    # Effective state follows workspace > role precedence. The legacy
    # paused_at/reason/by fields remain the effective display contract so old
    # clients stop immediately on a workspace hold; the explicit role_* fields
    # preserve the local desired state underneath that overlay.
    paused: bool = False
    pause_scope: Optional[Literal["workspace", "role"]] = None
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    paused_by: Optional[AgentStatusPausedBy] = None
    role_paused_at: Optional[datetime] = None
    role_paused_reason: Optional[str] = None
    role_paused_by: Optional[AgentStatusPausedBy] = None
    workspace_paused: bool = False
    workspace_paused_at: Optional[datetime] = None
    workspace_paused_reason: Optional[str] = None
    workspace_paused_by: Optional[AgentStatusPausedBy] = None
    workspace_control_version: int = 1
    last_run_at: Optional[datetime] = None
    bootstrap_status: Optional[str] = None
    bootstrap_error: Optional[str] = None
    bootstrap_started_at: Optional[datetime] = None
    bootstrap_completed_at: Optional[datetime] = None
    pending_decisions: int
    pending_breakdown: AgentStatusPendingBreakdown
    monthly_budget_cents: Optional[int] = None
    monthly_spent_cents: int
    current_run: Optional[AgentStatusCurrentRun] = None
    last_activity: Optional[AgentStatusActivity] = None


@router.get("/roles/{role_id}/agent/status", response_model=AgentStatusPayload)
def agent_status(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Consolidated agent state for the role — backs the top bar's poll.

    One call returns: enabled flag, paused state, monthly spend vs cap,
    in-flight cycle (if any), pending decision count, and the latest
    agent/recruiter event for the live tick.
    """
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")

    now = datetime.now(timezone.utc)
    # "pending" rolls up both decisions awaiting recruiter approve/override
    # and open orchestrator questions awaiting an answer. The Review queue
    # UI surfaces both kinds in one place — counts must follow.
    pending_decisions_count = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == current_user.organization_id,
            AgentDecision.role_id == role_id,
            AgentDecision.status == "pending",
            or_(
                AgentDecision.snoozed_until.is_(None),
                AgentDecision.snoozed_until <= now,
            ),
        )
        .count()
    )
    open_needs_input_count = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == current_user.organization_id,
            AgentNeedsInput.role_id == role_id,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .count()
    )
    pending = int(pending_decisions_count) + int(open_needs_input_count)

    current_run_row = (
        db.query(AgentRun)
        .filter(
            AgentRun.organization_id == current_user.organization_id,
            AgentRun.role_id == role_id,
            AgentRun.status == "running",
        )
        .order_by(desc(AgentRun.started_at))
        .first()
    )
    current_run = (
        AgentStatusCurrentRun(
            id=int(current_run_row.id),
            started_at=current_run_row.started_at,
            status=str(current_run_row.status),
            decisions_emitted=int(current_run_row.decisions_emitted or 0),
            tools_called=current_run_row.tools_called,
        )
        if current_run_row is not None
        else None
    )

    activity_row = (
        db.query(CandidateApplicationEvent, Candidate)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .outerjoin(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplicationEvent.organization_id == current_user.organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplicationEvent.actor_type.in_(("agent", "recruiter")),
        )
        .order_by(desc(CandidateApplicationEvent.created_at))
        .limit(1)
        .first()
    )
    last_activity = None
    if activity_row is not None:
        event, candidate = activity_row
        last_activity = AgentStatusActivity(
            event_type=str(event.event_type),
            reason=event.reason,
            actor_type=str(event.actor_type),
            application_id=int(event.application_id),
            candidate_name=getattr(candidate, "full_name", None) if candidate else None,
            created_at=event.created_at,
        )

    monthly_spent = budget_guard.month_to_date_spend_cents(db, role=role)

    role_paused_by = None
    if role.agent_paused_at is not None and budget_guard.is_manual_pause_reason(
        role.agent_paused_reason
    ):
        pause_actor = latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
            action=ROLE_CHANGE_ACTION_AGENT_PAUSED,
        )
        if pause_actor is not None:
            # The matching append-only event is the source of truth. Its user
            # can be unavailable after account deletion, but the event time is
            # still useful and must not be replaced with a different member.
            pause_actor_user_id = pause_actor.get("user_id")
            role_paused_by = AgentStatusPausedBy(
                user_id=(
                    int(pause_actor_user_id)
                    if pause_actor_user_id is not None
                    else None
                ),
                name=pause_actor.get("name"),
                is_current_user=(
                    pause_actor_user_id is not None
                    and int(pause_actor_user_id) == int(current_user.id)
                ),
                changed_at=pause_actor.get("changed_at"),
                attribution=(
                    "verified" if pause_actor_user_id is not None else "unavailable"
                ),
                source="role_change_event",
            )
        else:
            # Migration 169 introduced role_change_events without fabricating
            # history for already-paused roles. A sole surviving account that
            # predates such a pause is useful context, but remains explicitly
            # inferred because deleted historical users cannot be recovered.
            inferred_actor = infer_legacy_unique_org_actor(
                db,
                organization_id=int(current_user.organization_id),
                changed_at=role.agent_paused_at,
            )
            inferred_user_id = (
                inferred_actor.get("user_id") if inferred_actor is not None else None
            )
            role_paused_by = AgentStatusPausedBy(
                user_id=(
                    int(inferred_user_id) if inferred_user_id is not None else None
                ),
                name=(
                    inferred_actor.get("name") if inferred_actor is not None else None
                ),
                is_current_user=(
                    inferred_user_id is not None
                    and int(inferred_user_id) == int(current_user.id)
                ),
                changed_at=role.agent_paused_at,
                attribution=(
                    "inferred" if inferred_actor is not None else "unavailable"
                ),
                source=(
                    "legacy_unique_member"
                    if inferred_actor is not None
                    else "legacy_history"
                ),
            )

    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    workspace_paused_by = (
        AgentStatusPausedBy(**workspace_pause["paused_by"])
        if workspace_pause["paused_by"] is not None
        else None
    )
    enabled = bool(role.agentic_mode_enabled)
    if enabled and workspace_pause["paused"]:
        effective_paused = True
        pause_scope: Literal["workspace", "role"] | None = "workspace"
        effective_paused_at = workspace_pause["paused_at"]
        effective_paused_reason = workspace_pause["reason"]
        effective_paused_by = workspace_paused_by
    elif enabled and role.agent_paused_at is not None:
        effective_paused = True
        pause_scope = "role"
        effective_paused_at = role.agent_paused_at
        effective_paused_reason = role.agent_paused_reason
        effective_paused_by = role_paused_by
    else:
        effective_paused = False
        pause_scope = None
        effective_paused_at = None
        effective_paused_reason = None
        effective_paused_by = None

    return AgentStatusPayload(
        role_id=role_id,
        enabled=enabled,
        can_control_agent=has_job_permission_for_role(
            db,
            current_user=current_user,
            role=role,
            permission=JobPermission.CONTROL_AGENT,
        ),
        paused=effective_paused,
        pause_scope=pause_scope,
        paused_at=effective_paused_at,
        paused_reason=effective_paused_reason,
        paused_by=effective_paused_by,
        role_paused_at=role.agent_paused_at,
        role_paused_reason=role.agent_paused_reason,
        role_paused_by=role_paused_by,
        workspace_paused=bool(workspace_pause["paused"]),
        workspace_paused_at=workspace_pause["paused_at"],
        workspace_paused_reason=workspace_pause["reason"],
        workspace_paused_by=workspace_paused_by,
        workspace_control_version=int(workspace_pause["version"]),
        last_run_at=role.agent_last_run_at,
        bootstrap_status=getattr(role, "agent_bootstrap_status", None),
        bootstrap_error=getattr(role, "agent_bootstrap_error", None),
        bootstrap_started_at=getattr(role, "agent_bootstrap_started_at", None),
        bootstrap_completed_at=getattr(role, "agent_bootstrap_completed_at", None),
        pending_decisions=pending,
        pending_breakdown=AgentStatusPendingBreakdown(
            total=pending,
            decisions=int(pending_decisions_count),
            questions=int(open_needs_input_count),
        ),
        monthly_budget_cents=role.monthly_usd_budget_cents,
        monthly_spent_cents=monthly_spent,
        current_run=current_run,
        last_activity=last_activity,
    )


@router.get("/roles/{role_id}/agent/activity", response_model=AgentActivityPayload)
def agent_activity(
    role_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    before: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reverse-chronological feed of what the agent has been doing on this role.

    Merges four sources, all already persisted by the runtime:
      * agent_runs           — cycle started/finished/failed/paused
      * agent_decisions      — what got scored and recommended
      * candidate_application_events (actor=agent) — stage moves it made
      * agent_needs_input    — questions the agent raised + their resolution

    Cursor pagination via ``before`` (ISO timestamp). ``has_more`` is a
    cheap hint — true iff any source returned exactly ``limit`` rows.
    """
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail=f"role {role_id} not found")

    entries, has_more = build_activity_feed(
        db,
        organization_id=current_user.organization_id,
        role_id=role_id,
        limit=limit,
        before=before,
    )
    return AgentActivityPayload(role_id=role_id, entries=entries, has_more=has_more)
