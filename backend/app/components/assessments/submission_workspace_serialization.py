"""Workspace lock boundary for assessment submission entry points."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...domains.assessments_runtime.workspace_serialization import (
    assessment_workspace_mutex,
    prepare_assessment_workspace_mutex,
)
from ...models.assessment import Assessment


@contextmanager
def serialized_submission_assessment(
    db: Session,
    assessment: Assessment,
    *,
    workspace_lock_held: bool,
) -> Iterator[Assessment]:
    """Yield a fresh row under the mutex without waiting on an app checkout."""

    if workspace_lock_held:
        yield assessment
        return
    assessment_id = int(assessment.id)
    prepare_assessment_workspace_mutex(db)
    with assessment_workspace_mutex(db, assessment_id=assessment_id):
        refreshed = (
            db.query(Assessment)
            .filter(Assessment.id == assessment_id)
            .populate_existing()
            .one_or_none()
        )
        if refreshed is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Assessment not found")
        yield refreshed


__all__ = ["serialized_submission_assessment"]
