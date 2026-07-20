from types import SimpleNamespace
from unittest.mock import ANY, patch

import pytest

from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.services.task_approval_service import (
    TaskApprovalError,
    approve_task_for_use,
    provision_and_validate_task_repository,
    task_repository_readiness,
)


def _draft(db) -> Task:
    org = Organization(name="Approval Org", slug=f"approval-{id(db)}")
    db.add(org)
    db.flush()
    task = Task(
        organization_id=org.id,
        name="Generated Task",
        task_key=f"generated_{id(db)}",
        is_active=False,
        repo_structure={"files": {"README.md": "# Task", "src/main.py": "pass\n"}},
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
        },
    )
    db.add(task)
    db.flush()
    return task


def test_approve_task_for_use_sets_active_only_after_snapshot_verification(db):
    task = _draft(db)
    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        return_value={
            "source": "frozen_task_snapshot",
            "file_count": 2,
            "sha256": "a" * 64,
        },
    ) as provision:
        approved = approve_task_for_use(db, task, user_id=42)

    assert approved is task
    assert task.is_active is True
    assert task.extra_data["needs_review"] is False
    assert task.extra_data["approved_by_user_id"] == 42
    assert task.extra_data["repository_ready"] == {
        "verified_at": task.extra_data["repository_ready"]["verified_at"],
        "source": "frozen_task_snapshot",
        "file_count": 2,
        "sha256": "a" * 64,
    }
    assert "repo_url" not in task.extra_data["repository_ready"]
    provision.assert_called_once_with(task, settings_obj=ANY)


def test_approving_first_linked_task_restores_role_assessment_stage(db):
    task = _draft(db)
    role = Role(
        organization_id=task.organization_id,
        name="Task approval role",
        auto_skip_assessment=True,
    )
    role.tasks.append(task)
    db.add(role)
    db.flush()

    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        return_value={
            "source": "frozen_task_snapshot",
            "file_count": 2,
            "sha256": "a" * 64,
        },
    ):
        approve_task_for_use(db, task, user_id=42)

    assert task.is_active is True
    assert role.auto_skip_assessment is False


def test_approve_task_for_use_failure_never_mutates_activation_state(db):
    task = _draft(db)
    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        side_effect=TaskApprovalError("workspace manifest invalid"),
    ):
        with pytest.raises(TaskApprovalError, match="manifest invalid"):
            approve_task_for_use(db, task, user_id=42)

    assert task.is_active is False
    assert task.extra_data["needs_review"] is True
    assert "approved_by_user_id" not in task.extra_data


@pytest.mark.parametrize("verdict", [None, "fail"])
def test_generated_task_requires_passing_battle_test_before_approval(db, verdict):
    task = _draft(db)
    extra = dict(task.extra_data or {})
    extra["battle_test"] = ({"verdict": verdict} if verdict else None)
    task.extra_data = extra

    with pytest.raises(TaskApprovalError, match="battle test"):
        approve_task_for_use(db, task, user_id=42)

    assert task.is_active is False
    assert task.extra_data["needs_review"] is True


def test_provision_and_readiness_validate_frozen_manifest_without_github(
    db, monkeypatch, tmp_path
):
    task = _draft(db)
    mock_root = tmp_path / "github"
    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    settings_obj = SimpleNamespace(
        GITHUB_ORG="approval-org",
        GITHUB_TOKEN="",
        GITHUB_MOCK_MODE=False,
    )

    snapshot = provision_and_validate_task_repository(
        task,
        settings_obj=settings_obj,
    )
    ready, detail = task_repository_readiness(task, settings_obj=settings_obj)

    assert snapshot["source"] == "frozen_task_snapshot"
    assert snapshot["file_count"] == 2
    assert len(snapshot["sha256"]) == 64
    assert ready is True
    assert detail is None
    assert not mock_root.exists()


def test_repository_readiness_fails_for_unsafe_frozen_manifest(db):
    task = _draft(db)
    task.repo_structure = {"files": {".git/config": "credential = leaked"}}

    ready, detail = task_repository_readiness(task)

    assert ready is False
    assert "unsafe workspace manifest" in str(detail).lower()


def test_approval_rejects_manifest_file_parent_conflict_without_activation(db):
    task = _draft(db)
    task.repo_structure = {
        "files": {
            "src": "file\n",
            "src/main.py": "child\n",
        }
    }
    db.flush()

    with pytest.raises(TaskApprovalError, match="file/parent conflict"):
        approve_task_for_use(db, task, user_id=42)

    assert task.is_active is False
    assert task.extra_data["needs_review"] is True
    assert "repository_ready" not in task.extra_data


def test_repository_readiness_fails_for_empty_frozen_manifest(db):
    task = _draft(db)
    task.repo_structure = {"files": {}}

    ready, detail = task_repository_readiness(task)

    assert ready is False
    assert "no workspace files to publish" in str(detail).lower()
