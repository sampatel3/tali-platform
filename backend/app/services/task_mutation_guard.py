"""Serialize assessment-task changes with candidate invite creation.

Assessment sends hold the workspace ``Organization`` and live ``Role`` rows
while they resolve an active task and provision the candidate-facing branch.
Any mutation that changes a Task or its role linkage must take the same locks
first.  Keeping the order here prevents both stale sends and Role/Task lock
inversions across the API, catalogue sync, and activation workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.organization import Organization
from ..models.role import Role, role_tasks
from ..models.task import Task


class TaskMutationScopeChanged(RuntimeError):
    """A newly linked workspace appeared while the lock scope was acquired."""


@dataclass(frozen=True)
class TaskMutationBoundary:
    organizations: tuple[Organization, ...]
    roles: tuple[Role, ...]
    tasks: tuple[Task, ...]

    def task(self, task_id: int) -> Task | None:
        return next(
            (row for row in self.tasks if int(row.id) == int(task_id)),
            None,
        )

    def role(self, role_id: int) -> Role | None:
        return next(
            (row for row in self.roles if int(row.id) == int(role_id)),
            None,
        )


def _ids(values: Iterable[int]) -> list[int]:
    return sorted({int(value) for value in values})


def lock_task_mutation_boundary(
    db: Session,
    *,
    task_ids: Iterable[int] = (),
    role_ids: Iterable[int] = (),
    organization_ids: Iterable[int] = (),
) -> TaskMutationBoundary:
    """Lock ``Organization -> Role -> Task`` for one task/link mutation.

    Discovery runs under ``no_autoflush`` so a caller-owned dirty Task or
    relationship cannot be written before its authority rows are locked.
    Linked roles are included automatically; explicit role/org ids cover link
    creation and orphan task mutations.
    """

    requested_task_ids = _ids(task_ids)
    requested_role_ids = set(_ids(role_ids))
    requested_org_ids = set(_ids(organization_ids))

    with db.no_autoflush:
        if requested_task_ids:
            task_scope = (
                db.query(Task.id, Task.organization_id)
                .filter(Task.id.in_(requested_task_ids))
                .all()
            )
            requested_org_ids.update(
                int(org_id) for _task_id, org_id in task_scope if org_id is not None
            )
            linked_scope = (
                db.query(Role.id, Role.organization_id)
                .join(role_tasks, role_tasks.c.role_id == Role.id)
                .filter(role_tasks.c.task_id.in_(requested_task_ids))
                .all()
            )
            requested_role_ids.update(int(role_id) for role_id, _org_id in linked_scope)
            requested_org_ids.update(int(org_id) for _role_id, org_id in linked_scope)

        if requested_role_ids:
            explicit_role_scope = (
                db.query(Role.id, Role.organization_id)
                .filter(Role.id.in_(sorted(requested_role_ids)))
                .all()
            )
            requested_org_ids.update(
                int(org_id) for _role_id, org_id in explicit_role_scope
            )

        organizations = tuple(
            db.query(Organization)
            .filter(Organization.id.in_(sorted(requested_org_ids)))
            .order_by(Organization.id.asc())
            .with_for_update(of=Organization)
            .populate_existing()
            .all()
        ) if requested_org_ids else ()
        roles = tuple(
            db.query(Role)
            .filter(Role.id.in_(sorted(requested_role_ids)))
            .order_by(Role.id.asc())
            .with_for_update(of=Role)
            # Preserve caller-owned dirty Role fields (for example, update_role
            # may approve a generated task in the same unit of work).
            .all()
        ) if requested_role_ids else ()
        tasks = tuple(
            db.query(Task)
            .filter(Task.id.in_(requested_task_ids))
            .order_by(Task.id.asc())
            .with_for_update(of=Task)
            .populate_existing()
            .all()
        ) if requested_task_ids else ()
        if requested_task_ids:
            current_linked_org_ids = {
                int(org_id)
                for (org_id,) in (
                    db.query(Role.organization_id)
                    .join(role_tasks, role_tasks.c.role_id == Role.id)
                    .filter(role_tasks.c.task_id.in_(requested_task_ids))
                    .distinct()
                    .all()
                )
            }
            locked_org_ids = {int(row.id) for row in organizations}
            if not current_linked_org_ids.issubset(locked_org_ids):
                raise TaskMutationScopeChanged(
                    "Task linkage changed while acquiring mutation locks; retry"
                )

    return TaskMutationBoundary(
        organizations=organizations,
        roles=roles,
        tasks=tasks,
    )


__all__ = [
    "TaskMutationBoundary",
    "TaskMutationScopeChanged",
    "lock_task_mutation_boundary",
]
