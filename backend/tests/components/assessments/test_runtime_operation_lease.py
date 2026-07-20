from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.components.assessments.repository import (
    claim_runtime_operation,
    release_runtime_operation,
    utcnow,
)
from app.models.assessment import Assessment, AssessmentStatus
from app.models.organization import Organization
from app.models.task import Task


def _assessment(db, *, status: AssessmentStatus = AssessmentStatus.IN_PROGRESS) -> Assessment:
    org = Organization(name="Runtime lease org", slug=f"runtime-lease-{id(db)}")
    task = Task(name="Runtime lease task", task_key=f"runtime-lease-{id(db)}")
    db.add_all([org, task])
    db.flush()
    row = Assessment(
        organization_id=org.id,
        task_id=task.id,
        token=f"runtime-lease-token-{id(db)}",
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_runtime_operation_lease_serializes_workspace_mutations(db) -> None:
    row = _assessment(db)

    first = claim_runtime_operation(row, db, kind="save")
    assert row.runtime_operation_id == first
    assert row.runtime_operation_kind == "save"

    with pytest.raises(HTTPException) as conflict:
        claim_runtime_operation(row, db, kind="submit")
    assert conflict.value.status_code == 409

    assert release_runtime_operation(row.id, db, first) is True
    db.refresh(row)
    assert row.runtime_operation_id is None

    second = claim_runtime_operation(row, db, kind="submit")
    assert second != first


def test_stale_runtime_operation_is_recoverable(db) -> None:
    row = _assessment(db)
    first = claim_runtime_operation(row, db, kind="claude_chat", stale_after_seconds=60)
    row.runtime_operation_started_at = utcnow() - timedelta(minutes=5)
    db.commit()

    second = claim_runtime_operation(row, db, kind="save", stale_after_seconds=60)

    assert second != first
    assert row.runtime_operation_kind == "save"


def test_terminal_assessment_cannot_claim_runtime_operation(db) -> None:
    row = _assessment(db, status=AssessmentStatus.COMPLETED)

    with pytest.raises(HTTPException) as conflict:
        claim_runtime_operation(row, db, kind="save")

    assert conflict.value.status_code == 409
    assert row.runtime_operation_id is None
