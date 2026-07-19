"""Fail-closed reference checks for permanent Task deletion."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.assessment import Assessment
from ...models.assessment_experiment import AssessmentExperimentArm
from ...models.role import role_tasks
from ...models.task_calibration import TaskCalibration


def task_content_reference_kinds(db: Session, *, task_id: int) -> tuple[str, ...]:
    """Return evidence/history relations that make task content immutable."""

    references: list[str] = []
    for name, model in (
        ("assessments", Assessment),
        ("calibrations", TaskCalibration),
        ("experiments", AssessmentExperimentArm),
    ):
        if db.query(model.id).filter(model.task_id == int(task_id)).first() is not None:
            references.append(name)
    return tuple(references)


def task_reference_kinds(db: Session, *, task_id: int) -> tuple[str, ...]:
    """Return every durable relation that still depends on ``task_id``."""

    references: list[str] = []
    if (
        db.query(role_tasks.c.task_id)
        .filter(role_tasks.c.task_id == int(task_id))
        .first()
        is not None
    ):
        references.append("role_assignments")
    references.extend(task_content_reference_kinds(db, task_id=int(task_id)))
    return tuple(references)


def require_task_unreferenced(db: Session, *, task_id: int) -> None:
    references = task_reference_kinds(db, task_id=int(task_id))
    if not references:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "TASK_STILL_REFERENCED",
            "references": list(references),
            "message": (
                "This task is still referenced and cannot be deleted. "
                "Retire or unlink every dependent record first."
            ),
        },
    )


__all__ = [
    "require_task_unreferenced",
    "task_content_reference_kinds",
    "task_reference_kinds",
]
