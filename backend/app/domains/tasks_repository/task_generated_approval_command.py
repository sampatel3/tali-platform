"""Serialized two-phase approval command for generated assessment tasks."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.task import Task
from ...models.user import User
from ...services.task_approval_service import (
    apply_prepared_task_approval,
    capture_task_approval,
    prepare_task_approval,
)
from ...services.task_repository_serialization import task_repository_write_mutex
from .task_role_scope import (
    add_linked_task_change_boundaries,
    assessment_stage_changes,
    lock_task_role_scope,
)


@dataclass(frozen=True)
class GeneratedTaskApprovalResult:
    task: Task
    role_versions: dict[int, int]
    changed_stage_role_ids: list[int]


def approve_generated_task_command(
    db: Session,
    *,
    task_id: int,
    current_user: User,
) -> GeneratedTaskApprovalResult:
    """Own the repository mutex from initial capture through final commit."""

    try:
        with task_repository_write_mutex(db, task_id=int(task_id)):
            scope = lock_task_role_scope(
                db,
                task_id=task_id,
                current_user=current_user,
            )
            task = scope.task
            extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
            if not extra.get("generated"):
                raise HTTPException(
                    status_code=400,
                    detail="Task is not a generated draft",
                )
            captured = capture_task_approval(task)
            actor_user_id = int(current_user.id)

            db.rollback()
            prepared = prepare_task_approval(captured)
            scope = lock_task_role_scope(
                db,
                task_id=task_id,
                current_user=current_user,
            )
            task = scope.task
            apply_prepared_task_approval(
                db,
                task,
                prepared,
                user_id=actor_user_id,
            )
            role_versions = add_linked_task_change_boundaries(
                db,
                scope=scope,
                current_user=current_user,
                action="role_task_approved",
                reason=f"generated assessment task {task.id} approved",
            )
            changed_stage_role_ids = assessment_stage_changes(scope)
            db.commit()
            db.refresh(task)
            return GeneratedTaskApprovalResult(
                task=task,
                role_versions=role_versions,
                changed_stage_role_ids=changed_stage_role_ids,
            )
    except Exception:
        db.rollback()
        raise


__all__ = [
    "GeneratedTaskApprovalResult",
    "approve_generated_task_command",
]
