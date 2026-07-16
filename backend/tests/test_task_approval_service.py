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


def test_approve_task_for_use_sets_active_only_after_repo_verification(db):
    task = _draft(db)
    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        return_value="https://github.com/example/generated.git",
    ) as provision:
        approved = approve_task_for_use(db, task, user_id=42)

    assert approved is task
    assert task.is_active is True
    assert task.extra_data["needs_review"] is False
    assert task.extra_data["approved_by_user_id"] == 42
    assert task.extra_data["repository_ready"]["repo_url"].endswith("generated.git")
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
        return_value="https://github.com/example/generated.git",
    ):
        approve_task_for_use(db, task, user_id=42)

    assert task.is_active is True
    assert role.auto_skip_assessment is False


def test_approve_task_for_use_failure_never_mutates_activation_state(db):
    task = _draft(db)
    with patch(
        "app.services.task_approval_service.provision_and_validate_task_repository",
        side_effect=TaskApprovalError("GitHub main missing"),
    ):
        with pytest.raises(TaskApprovalError, match="main missing"):
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


def test_provision_and_readiness_validate_the_exact_mock_repo(
    db, monkeypatch, tmp_path
):
    task = _draft(db)
    mock_root = tmp_path / "github"
    local_root = tmp_path / "local"
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(mock_root))
    monkeypatch.setenv("TASK_REPOS_ROOT", str(local_root))
    settings_obj = SimpleNamespace(
        GITHUB_ORG="approval-org",
        GITHUB_TOKEN="mock-token",
    )

    repo_url = provision_and_validate_task_repository(
        task,
        settings_obj=settings_obj,
    )
    ready, detail = task_repository_readiness(task, settings_obj=settings_obj)

    assert repo_url == f"mock://approval-org/{task.task_key}"
    assert ready is True
    assert detail is None
    assert (mock_root / "approval-org" / task.task_key / ".git").is_dir()


def test_repository_readiness_fails_for_missing_task_specific_repo(
    db, monkeypatch, tmp_path
):
    task = _draft(db)
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_MOCK_ROOT", str(tmp_path / "missing"))
    settings_obj = SimpleNamespace(
        GITHUB_ORG="approval-org",
        GITHUB_TOKEN="mock-token",
    )

    ready, detail = task_repository_readiness(task, settings_obj=settings_obj)

    assert ready is False
    assert "does not exist" in str(detail)
