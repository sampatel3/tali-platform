from __future__ import annotations

import sys

import pytest
from fastapi import HTTPException

from app.domains.tasks_repository.task_reference_guard import (
    require_task_unreferenced,
    task_reference_kinds,
)
from app.domains.tasks_repository.task_update_policy import (
    require_unreferenced_assessment_content,
)
from app.models.assessment import Assessment
from app.models.assessment_experiment import (
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.task_calibration import TaskCalibration
from app.services.task_catalog import sync_template_task_specs


def _referenced_task(db) -> tuple[Organization, Role, Task]:
    org = Organization(name="Task reference org", slug=f"task-reference-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Reference role")
    task = Task(
        organization_id=None,
        name="Reference template",
        task_key=f"reference-template-{id(db)}",
        is_template=True,
        is_active=True,
    )
    role.tasks.append(task)
    db.add(role)
    db.flush()
    return org, role, task


def test_reference_guard_covers_role_history_calibration_and_experiment(db):
    org, role, task = _referenced_task(db)
    assessment = Assessment(
        organization_id=org.id,
        role_id=role.id,
        task_id=task.id,
        token=f"reference-assessment-{id(db)}",
    )
    calibration = TaskCalibration(
        organization_id=org.id,
        task_id=task.id,
        role_family="backend",
        predictive_quality=0.5,
        sample_size=10,
    )
    experiment = AssessmentExperiment(
        organization_id=org.id,
        role_id=role.id,
        key=f"reference-experiment-{id(db)}",
        name="Reference experiment",
        salt="reference-salt",
    )
    db.add_all([assessment, calibration, experiment])
    db.flush()
    db.add(
        AssessmentExperimentArm(
            experiment_id=experiment.id,
            arm_key="A",
            task_id=task.id,
        )
    )
    db.commit()

    assert task_reference_kinds(db, task_id=task.id) == (
        "role_assignments",
        "assessments",
        "calibrations",
        "experiments",
    )
    with pytest.raises(HTTPException) as delete_error:
        require_task_unreferenced(db, task_id=task.id)
    assert delete_error.value.status_code == 409

    with pytest.raises(HTTPException) as update_error:
        require_unreferenced_assessment_content(
            db,
            task=task,
            payload={"name": "Changed historical template"},
        )
    assert update_error.value.detail["code"] == "TASK_VERSION_REQUIRED"
    assert update_error.value.detail["references"] == [
        "assessments",
        "calibrations",
        "experiments",
    ]


def test_role_assignment_alone_allows_versioned_edit_but_not_deletion(db):
    _, _, task = _referenced_task(db)
    db.commit()

    require_unreferenced_assessment_content(
        db,
        task=task,
        payload={"name": "Safely recertified future task"},
    )
    with pytest.raises(HTTPException):
        require_task_unreferenced(db, task_id=task.id)


def test_template_sync_preserves_referenced_content_and_active_assignment(db):
    org, role, task = _referenced_task(db)
    original_name = task.name
    original_scenario = task.scenario
    db.add(
        Assessment(
            organization_id=org.id,
            role_id=role.id,
            task_id=task.id,
            token=f"sync-history-{id(db)}",
        )
    )
    db.commit()

    changed = sync_template_task_specs(
        db,
        [
            {
                "task_id": task.task_key,
                "name": "Mutated catalogue content",
                "role": "backend_engineer",
                "duration_minutes": 45,
                "scenario": "New content that must become a new version.",
                "repo_structure": {"files": {"README.md": "changed"}},
                "evaluation_rubric": {"quality": {"weight": 1.0}},
            }
        ],
    )

    db.refresh(task)
    assert changed["updated"] == 0
    assert changed["version_required"] == 1
    assert task.name == original_name
    assert task.scenario == original_scenario
    assert task.is_active is True

    removed = sync_template_task_specs(db, [])
    db.refresh(task)
    assert removed["preserved_referenced"] == 1
    assert task.is_active is True
    assert [linked.id for linked in role.tasks] == [task.id]


def test_delete_template_cli_refuses_referenced_task(db, monkeypatch, capsys):
    from app.scripts import delete_template_task

    _, _, task = _referenced_task(db)
    db.commit()
    task_id = int(task.id)
    task_key = str(task.task_key)
    monkeypatch.setattr(delete_template_task, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(sys, "argv", ["delete_template_task", task_key])

    with pytest.raises(SystemExit) as exc_info:
        delete_template_task.main()

    assert exc_info.value.code == 1
    assert "role_assignments" in capsys.readouterr().err
    db.expire_all()
    assert db.get(Task, task_id) is not None


def test_template_catalog_sync_is_idempotent_without_destructive_cleanup(db):
    task_key = f"idempotent-catalog-{id(db)}"
    spec = {
        "task_id": task_key,
        "name": "Idempotent catalogue task",
        "role": "backend_engineer",
        "duration_minutes": 45,
        "scenario": "The same catalogue input must create only one row.",
        "repo_structure": {"files": {"README.md": "stable"}},
        "evaluation_rubric": {"quality": {"weight": 1.0}},
    }

    first = sync_template_task_specs(db, [spec])
    second = sync_template_task_specs(db, [spec])

    assert first["created"] == 1
    assert second == {
        "created": 0,
        "updated": 0,
        "deactivated": 0,
        "preserved_referenced": 0,
        "version_required": 0,
    }
    assert (
        db.query(Task)
        .filter(
            Task.task_key == task_key,
            Task.organization_id.is_(None),
            Task.is_template.is_(True),
        )
        .count()
        == 1
    )
