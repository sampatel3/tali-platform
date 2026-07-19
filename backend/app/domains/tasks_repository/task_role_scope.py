"""Authorization and concurrency boundaries for shared assessment tasks.

Tasks are organization-owned, but linking one to a role makes changes to that
task part of the role's shared agent configuration.  Task-management routes
therefore need the same per-job authorization, revision, and audit boundary as
the role routes before they mutate a linked task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.role import Role, role_tasks
from ...models.task import Task
from ...models.user import User
from ...services.agent_activation_checklist import (
    resolve_satisfied_activation_questions,
    surface_activation_questions,
)
from ..assessments_runtime.job_authorization import (
    JobPermission,
    has_job_permission_for_role,
)
from ..assessments_runtime.role_management_route_support import (
    _add_role_change_boundary,
)
from ...services.role_change_audit import capture_role_change_snapshot

logger = logging.getLogger("taali.tasks.role_scope")


@dataclass(frozen=True)
class LockedTaskRoleScope:
    task: Task
    roles: tuple[Role, ...]
    linked_role_ids: tuple[int, ...]
    active_before: dict[int, bool]
    role_before: dict[int, dict]


def _linked_role_ids(db: Session, *, task_id: int) -> tuple[int, ...]:
    rows = db.execute(
        role_tasks.select()
        .with_only_columns(role_tasks.c.role_id)
        .where(role_tasks.c.task_id == int(task_id))
        .order_by(role_tasks.c.role_id.asc())
    ).all()
    return tuple(int(row[0]) for row in rows)


def lock_task_role_scope(
    db: Session,
    *,
    task_id: int,
    current_user: User,
) -> LockedTaskRoleScope:
    """Lock and authorize every live role affected by a task mutation.

    The canonical lock order is Role (ascending id), then Task.  Association
    writers follow the same order and also lock the Task row.  Re-reading the
    association after both locks turns a concurrent link/unlink into a safe
    retry instead of an authorization gap.
    """

    organization_id = getattr(current_user, "organization_id", None)
    task = (
        db.query(Task)
        .filter(Task.id == int(task_id), Task.organization_id == organization_id)
        .first()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    expected_role_ids = _linked_role_ids(db, task_id=int(task_id))
    roles: list[Role] = []
    if expected_role_ids:
        locked_roles = (
            db.query(Role)
            .filter(Role.id.in_(expected_role_ids))
            .order_by(Role.id.asc())
            .with_for_update(of=Role)
            .populate_existing()
            .all()
        )
        roles = [role for role in locked_roles if role.deleted_at is None]
        if (
            len(locked_roles) != len(expected_role_ids)
            or any(role.organization_id != organization_id for role in locked_roles)
            or any(
                not has_job_permission_for_role(
                    db,
                    current_user=current_user,
                    role=role,
                    permission=JobPermission.CONTROL_AGENT,
                )
                for role in roles
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )

    locked_task = (
        db.query(Task)
        .filter(Task.id == int(task_id), Task.organization_id == organization_id)
        .with_for_update(of=Task)
        .populate_existing()
        .first()
    )
    if locked_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if _linked_role_ids(db, task_id=int(task_id)) != expected_role_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task assignments changed; refresh and retry.",
        )

    return LockedTaskRoleScope(
        task=locked_task,
        roles=tuple(roles),
        linked_role_ids=expected_role_ids,
        active_before={
            int(role.id): any(bool(linked.is_active) for linked in (role.tasks or []))
            for role in roles
        },
        role_before={
            int(role.id): capture_role_change_snapshot(role) for role in roles
        },
    )


def add_linked_task_change_boundaries(
    db: Session,
    *,
    scope: LockedTaskRoleScope,
    current_user: User,
    action: str,
    reason: str,
) -> dict[int, int]:
    """Version/audit linked roles and reconcile their activation questions."""

    versions: dict[int, int] = {}
    for role in scope.roles:
        versions[int(role.id)] = _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action=action,
            reason=reason,
            before=scope.role_before[int(role.id)],
        )
        has_active_task = any(bool(task.is_active) for task in (role.tasks or []))
        if (
            bool(role.agentic_mode_enabled)
            and not bool(role.auto_skip_assessment)
            and not has_active_task
        ):
            surface_activation_questions(db, role=role)
        else:
            resolve_satisfied_activation_questions(db, role=role)
    return versions


def assessment_stage_changes(
    scope: LockedTaskRoleScope,
) -> list[int]:
    """Return linked roles whose effective assessment-stage presence flipped."""

    changed: list[int] = []
    for role in scope.roles:
        if bool(role.auto_skip_assessment):
            continue
        after = any(bool(task.is_active) for task in (role.tasks or []))
        if scope.active_before.get(int(role.id), False) != after:
            changed.append(int(role.id))
    return changed


def apply_task_update_boundaries(
    db: Session,
    *,
    scope: LockedTaskRoleScope,
    current_user: User,
    update_data: dict,
    approval_invalidated: bool,
) -> tuple[bool, dict[int, int], list[int]]:
    """Apply task fields plus any linked-role intent/version consequences."""

    task = scope.task
    changed = any(
        getattr(task, key, None) != value for key, value in update_data.items()
    )
    was_active = bool(task.is_active)
    for key, value in update_data.items():
        setattr(task, key, value)
    if not changed or not scope.roles:
        return changed, {}, []
    if was_active and not bool(task.is_active):
        block_selected_task_activation_intents(
            scope,
            reason=(
                "The assessment task selected for Turn on was deactivated. "
                "Review the updated task, choose another task, or skip the "
                "assessment stage, then press Turn on again."
            ),
        )
    versions = add_linked_task_change_boundaries(
        db,
        scope=scope,
        current_user=current_user,
        action=(
            "role_task_approval_invalidated"
            if approval_invalidated
            else "role_task_updated"
        ),
        reason=f"assessment task {task.id} updated",
    )
    return changed, versions, assessment_stage_changes(scope)


def require_unlinked_task(scope: LockedTaskRoleScope, *, operation: str) -> None:
    if not scope.roles:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Cannot {operation} a task while it is assigned to a role. "
            "Unlink it through each role's versioned task controls first."
        ),
    )


def block_selected_task_activation_intents(
    scope: LockedTaskRoleScope,
    *,
    reason: str,
) -> int:
    """Block durable Turn-on commands whose exact selected task went away."""

    from ...services.role_activation_intent import (
        block_activation_intent_for_unavailable_selected_task,
    )

    return sum(
        int(
            block_activation_intent_for_unavailable_selected_task(
                role,
                task_id=int(scope.task.id),
                reason=reason,
            )
        )
        for role in scope.roles
    )


def reconcile_assessment_stage_changes(
    db: Session,
    *,
    role_ids: list[int],
    role_versions: dict[int, int],
) -> None:
    """Best-effort post-commit re-flow at each exact committed role revision."""

    from ...services.bulk_decision_service import (
        reconcile_pending_positive_decisions,
    )

    for role_id in role_ids:
        try:
            reconcile_pending_positive_decisions(
                db,
                role_id=int(role_id),
                expected_role_version=int(role_versions[role_id]),
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Assessment-stage reconcile failed after task mutation role_id=%s",
                role_id,
            )


__all__ = [
    "LockedTaskRoleScope",
    "add_linked_task_change_boundaries",
    "apply_task_update_boundaries",
    "assessment_stage_changes",
    "block_selected_task_activation_intents",
    "lock_task_role_scope",
    "reconcile_assessment_stage_changes",
    "require_unlinked_task",
]
