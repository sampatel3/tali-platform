"""Two-phase task PATCH command with lock-free repository synchronization."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.task import Task
from ...models.user import User
from ...platform.config import settings
from ...services.task_approval_service import task_approval_fingerprint
from ...services.task_repository_serialization import task_repository_write_mutex
from .task_role_scope import (
    LockedTaskRoleScope,
    apply_task_update_boundaries,
    lock_task_role_scope,
    reconcile_assessment_stage_changes,
)
from .task_update_policy import (
    ensure_repo_structure,
    normalize_task_payload,
    prepare_task_update,
    protect_system_task_metadata,
    require_unreferenced_assessment_content,
    task_repository_update_required,
)


@dataclass(frozen=True)
class CapturedTaskRepositoryUpdate:
    """Exact database state and detached proposed repository content."""

    source_fingerprint: str
    linked_role_ids: tuple[int, ...]
    proposed_task: Any


def _capture_repository_update(
    scope: LockedTaskRoleScope,
    *,
    update_data: dict[str, Any],
) -> CapturedTaskRepositoryUpdate:
    task = scope.task
    proposed = SimpleNamespace(
        **{
            column.key: copy.deepcopy(getattr(task, column.key, None))
            for column in Task.__table__.columns
        }
    )
    for key, value in update_data.items():
        setattr(proposed, key, copy.deepcopy(value))
    return CapturedTaskRepositoryUpdate(
        source_fingerprint=task_approval_fingerprint(task),
        linked_role_ids=scope.linked_role_ids,
        proposed_task=proposed,
    )


def _require_current_capture(
    scope: LockedTaskRoleScope,
    *,
    captured: CapturedTaskRepositoryUpdate,
) -> None:
    if (
        scope.linked_role_ids == captured.linked_role_ids
        and task_approval_fingerprint(scope.task) == captured.source_fingerprint
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "TASK_UPDATE_STALE",
            "message": (
                "The task or its role assignments changed while the repository "
                "was being synchronized. Refresh and retry your edit."
            ),
        },
    )


def _prepared_update(
    db: Session,
    *,
    scope: LockedTaskRoleScope,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    task = scope.task
    update_data = normalize_task_payload(payload)
    update_data = protect_system_task_metadata(update_data, current_task=task)
    if set(update_data) - {"is_active"}:
        update_data = ensure_repo_structure(update_data, fallback_task=task)
    require_unreferenced_assessment_content(db, task=task, payload=update_data)
    return prepare_task_update(task, update_data)


def _apply_and_commit(
    db: Session,
    *,
    scope: LockedTaskRoleScope,
    current_user: User,
    update_data: dict[str, Any],
    approval_invalidated: bool,
) -> Task:
    task = scope.task
    _changed, role_versions, changed_stage_role_ids = apply_task_update_boundaries(
        db,
        scope=scope,
        current_user=current_user,
        update_data=update_data,
        approval_invalidated=approval_invalidated,
    )
    db.flush()
    db.commit()
    db.refresh(task)
    reconcile_assessment_stage_changes(
        db,
        role_ids=changed_stage_role_ids,
        role_versions=role_versions,
    )
    return task


def _execute_task_update_serialized(
    db: Session,
    *,
    task_id: int,
    payload: dict[str, Any],
    current_user: User,
    recreate_repository: Callable[[Any], str],
    repository_service_factory: Callable[..., Any],
) -> Task:
    """Apply PATCH, keeping filesystem/GitHub work outside Role/Task locks."""

    scope = lock_task_role_scope(
        db,
        task_id=task_id,
        current_user=current_user,
    )
    update_data, approval_required, approval_invalidated = _prepared_update(
        db,
        scope=scope,
        payload=payload,
    )
    if approval_required:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Generated drafts must be activated through the explicit "
                "approval endpoint after battle-test and repository validation."
            ),
        )

    if not task_repository_update_required(scope.task, update_data):
        try:
            return _apply_and_commit(
                db,
                scope=scope,
                current_user=current_user,
                update_data=update_data,
                approval_invalidated=approval_invalidated,
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to update task",
            ) from exc

    captured = _capture_repository_update(scope, update_data=update_data)
    db.rollback()
    try:
        recreate_repository(captured.proposed_task)
        repository_service_factory(
            settings.GITHUB_ORG,
            settings.GITHUB_TOKEN,
        ).create_template_repo(captured.proposed_task)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update task") from exc

    try:
        scope = lock_task_role_scope(
            db,
            task_id=task_id,
            current_user=current_user,
        )
        _require_current_capture(scope, captured=captured)
        # An assessment may have been created while repository I/O ran. Recheck
        # immutability under the final canonical Role→Task locks.
        require_unreferenced_assessment_content(
            db,
            task=scope.task,
            payload=update_data,
        )
        return _apply_and_commit(
            db,
            scope=scope,
            current_user=current_user,
            update_data=update_data,
            approval_invalidated=approval_invalidated,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update task") from exc


def execute_task_update(
    db: Session,
    *,
    task_id: int,
    payload: dict[str, Any],
    current_user: User,
    recreate_repository: Callable[[Any], str],
    repository_service_factory: Callable[..., Any],
) -> Task:
    """Serialize all writers of the task's canonical repository branch."""

    with task_repository_write_mutex(db, task_id=int(task_id)):
        return _execute_task_update_serialized(
            db,
            task_id=task_id,
            payload=payload,
            current_user=current_user,
            recreate_repository=recreate_repository,
            repository_service_factory=repository_service_factory,
        )


__all__ = ["execute_task_update"]
