"""Live database guards for automatic role and assessment side effects.

Role-agent work is frequently queued before a recruiter changes the role.  A
worker must therefore authorize itself against the *current* locked Role row,
not the ORM object it received earlier in a scoring/agent cycle.  These helpers
are intentionally small so both the generic autonomy dispatcher and direct
candidate-contact actions share the same fail-closed contract.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.assessment import Assessment
from ..models.role import (
    JOB_STATUS_OPEN,
    ROLE_KIND_STANDARD,
    Role,
    role_tasks,
)
from ..models.task import Task
from .ats_role_lifecycle import ats_job_lifecycle


def lock_live_role(
    db: Session, *, role_id: int, organization_id: int
) -> Role | None:
    """Reload and lock the current Role row before an automatic side effect."""

    # Autoflush is disabled globally. Persist caller-owned changes (for
    # example an atomic Turn-on + first action) before ``populate_existing``
    # reloads the row, otherwise the live-row check can discard a legitimate
    # unflushed toggle and incorrectly hold the action.
    db.flush()
    # Workspace Pause/Resume serializes on the Organization row. Take that
    # lock before the Role lock so a just-paused workspace cannot race through
    # this automatic side-effect boundary, and so every provider admission
    # path follows the same org -> role lock order.
    from .workspace_agent_control import workspace_agent_control_snapshot

    workspace_agent_control_snapshot(
        db,
        organization_id=int(organization_id),
        lock=True,
    )
    return (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )


def automatic_role_action_block_reason(
    role: Role | None,
    *,
    db: Session | None = None,
) -> str | None:
    """Why new autonomous work cannot run against ``role`` right now.

    The local requisition and the linked ATS job are both execution
    authorities.  A queued task may outlive either lifecycle transition, so
    every paid model call and automatic side effect reuses this provider-neutral
    predicate immediately before it starts.  ``job_status is None`` remains
    permissive for legacy/manual roles that pre-date the managed requisition
    lifecycle.
    """

    if role is None:
        return "role is unavailable"
    if getattr(role, "deleted_at", None) is not None:
        return "role is deleted"
    if (
        getattr(role, "role_kind", None) or ROLE_KIND_STANDARD
    ) != ROLE_KIND_STANDARD:
        return "related role does not own autonomous actions"
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return "role agent is disabled"
    if db is not None:
        from .workspace_agent_control import workspace_agent_is_paused

        if workspace_agent_is_paused(
            db,
            organization_id=int(role.organization_id),
        ):
            return "workspace agent is paused"
    if getattr(role, "agent_paused_at", None) is not None:
        return "role agent is paused"

    job_status = getattr(role, "job_status", None)
    if job_status is not None and job_status != JOB_STATUS_OPEN:
        return f"job is not open (status: {job_status})"

    ats = ats_job_lifecycle(role)
    if ats.external_job_id and ats.external_job_live is False:
        provider = ats.provider or "ATS"
        return f"linked {provider} job is not live"
    return None


def assessment_task_is_current(
    db: Session, *, assessment: Assessment, role: Role
) -> bool:
    """Whether the assessment task remains active and linked to this role.

    Existing/in-progress attempts are retained when a requisition changes, but
    their old task must not silently drive a new-JD auto-advance or resend.
    Query the association table directly so relationship cache staleness cannot
    weaken the boundary.
    """

    task_id = getattr(assessment, "task_id", None)
    assessment_role_id = getattr(assessment, "role_id", None)
    if task_id is None or assessment_role_id is None:
        return False
    if int(assessment_role_id) != int(role.id):
        return False
    # Sessions are configured with autoflush disabled. Make relationship
    # changes made earlier in this same caller-owned transaction visible to the
    # association-table query without committing them. This preserves the
    # durable/current-row check while allowing link-and-send atomically.
    db.flush()
    row = (
        db.query(Task.id)
        .join(role_tasks, role_tasks.c.task_id == Task.id)
        .filter(
            role_tasks.c.role_id == int(role.id),
            Task.id == int(task_id),
            Task.organization_id == int(role.organization_id),
            Task.is_active.is_(True),
        )
        .first()
    )
    return row is not None


__all__ = [
    "assessment_task_is_current",
    "automatic_role_action_block_reason",
    "lock_live_role",
]
