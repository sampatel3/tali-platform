"""Serialized canonical-repository operations used by assessment creation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.assessment import Assessment
from ..models.role import Role, role_tasks
from ..models.task import Task
from .assessment_repository_service import AssessmentRepositoryError
from .task_repository_serialization import (
    TaskRepositoryBusyError,
    task_repository_write_mutex,
)


def create_serialized_assessment_branch(
    db: Session,
    repository_service: Any,
    assessment: Assessment,
    *,
    wait_for_repository: bool = False,
) -> Any:
    """Revalidate assessment task authority inside its repo mutex, then branch.

    Assessment flows generally reach this seam after locking billing,
    application, Role, or Assessment rows. Their default is therefore a
    non-blocking advisory attempt: contention fails quickly so the caller can
    roll back/retry instead of deadlocking a repository writer that is waiting
    for one of those rows.
    """

    assessment_id = int(assessment.id)
    task_id = int(assessment.task_id)
    try:
        with task_repository_write_mutex(
            db,
            task_id=task_id,
            wait=wait_for_repository,
        ):
            identity = (
                db.query(
                    Assessment.organization_id,
                    Assessment.role_id,
                    Assessment.task_id,
                )
                .filter(Assessment.id == assessment_id)
                .one_or_none()
            )
            if identity is None or int(identity.task_id) != task_id:
                raise AssessmentRepositoryError(
                    f"Assessment {assessment_id} changed before repository branching"
                )
            organization_id = int(identity.organization_id)
            if identity.role_id is not None:
                role_id = int(identity.role_id)
                role = (
                    db.query(Role)
                    .filter(
                        Role.id == role_id,
                        Role.organization_id == organization_id,
                        Role.deleted_at.is_(None),
                    )
                    .populate_existing()
                    .with_for_update(of=Role)
                    .one_or_none()
                )
                link = db.execute(
                    role_tasks.select()
                    .where(
                        role_tasks.c.role_id == role_id,
                        role_tasks.c.task_id == task_id,
                    )
                    .with_for_update()
                ).first()
                if role is None or link is None:
                    raise AssessmentRepositoryError(
                        f"Task {task_id} is no longer active and assignable to "
                        f"assessment {assessment_id}"
                    )
            task = (
                db.query(Task)
                .filter(
                    Task.id == task_id,
                    Task.is_active.is_(True),
                    or_(
                        Task.organization_id == organization_id,
                        and_(
                            Task.organization_id.is_(None),
                            Task.is_template.is_(True),
                        ),
                    ),
                )
                .populate_existing()
                .with_for_update(of=Task)
                .one_or_none()
            )
            if task is None:
                raise AssessmentRepositoryError(
                    f"Task {task_id} is no longer active and assignable to "
                    f"assessment {assessment_id}"
                )
            branch = repository_service.create_assessment_branch(
                task,
                assessment_id,
            )
            assessment.assessment_repo_url = branch.repo_url
            assessment.assessment_branch = branch.branch_name
            assessment.clone_command = branch.clone_command
            return branch
    except TaskRepositoryBusyError as exc:
        raise AssessmentRepositoryError(
            f"Task {task_id} repository is temporarily busy; retry"
        ) from exc


__all__ = ["create_serialized_assessment_branch"]
