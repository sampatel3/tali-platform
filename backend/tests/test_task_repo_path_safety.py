from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.organization import Organization
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskUpdate
from app.services.assessment_repository_service import AssessmentRepositoryService
from app.services.assessment_repository_types import AssessmentRepositoryError
from app.services.task_approval_service import TaskApprovalError, _validate_repository_definition
from app.services.task_repo_service import (
    _write_repo_files as write_task_repo_files,
    is_safe_repo_file_path,
    is_safe_repository_name,
    normalize_repo_file_content,
    normalize_repo_files,
    recreate_task_main_repo,
    task_template_repository_name,
)


@pytest.mark.parametrize(
    "path",
    [
        "/absolute.py",
        "../escape.py",
        "nested/../../escape.py",
        ".git/config",
        "nested/.GIT/hooks/pre-commit",
        "C:\\absolute.py",
        "bad\x00path.py",
    ],
)
def test_repo_control_and_escape_paths_are_unsafe(path: str) -> None:
    assert is_safe_repo_file_path(path) is False


def test_repo_file_normalization_ignores_non_string_list_paths() -> None:
    normalized = normalize_repo_files(
        {
            "files": [
                {"path": {"not": "hashable"}, "content": "ignored"},
                {"path": "src/main.py", "content": "print('safe')"},
            ]
        }
    )

    assert normalized == {"src/main.py": "print('safe')"}


def test_repo_file_escape_normalization_preserves_unicode() -> None:
    assert normalize_repo_file_content("café 😀\\nnext\\tline") == (
        "café 😀\nnext\tline"
    )
    assert normalize_repo_file_content(r"literal\\n") == r"literal\n"


@pytest.mark.parametrize("schema", [TaskCreate, TaskUpdate])
def test_task_api_schemas_reject_git_control_paths(schema) -> None:
    payload = {
        "repo_structure": {
            "files": {
                "README.md": "safe",
                ".git/config": "[core]\nfsmonitor = malicious-command",
            }
        }
    }
    if schema is TaskCreate:
        payload.update(
            {
                "name": "Safe task",
                "description": "Safe task description",
                "task_type": "debugging",
                "difficulty": "mid",
                "starter_code": "pass",
                "test_code": "assert True",
            }
        )

    with pytest.raises(ValidationError, match="unsafe file path"):
        schema.model_validate(payload)


def test_approval_rejects_git_control_paths() -> None:
    task = SimpleNamespace(
        id=7,
        repo_structure={
            "files": {
                "README.md": "safe",
                ".git/config": "malicious",
            }
        },
    )

    with pytest.raises(TaskApprovalError) as caught:
        _validate_repository_definition(task)

    assert caught.value.code == "task_repository_definition_unsafe"


def test_local_snapshot_never_writes_supplied_git_control_files(
    monkeypatch, tmp_path
) -> None:
    marker = "malicious-fsmonitor-command"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path))
    task = SimpleNamespace(
        id=11,
        organization_id=3,
        task_key="path-safety",
        name="Path safety",
        repo_structure={
            "files": {
                "src/main.py": "print('safe')\n",
                ".git/config": f"[core]\nfsmonitor = {marker}\n",
            }
        },
    )

    repo = recreate_task_main_repo(task)

    assert (tmp_path / "path-safety-path-safety-3-11" / "src" / "main.py").is_file()
    assert marker not in (tmp_path / "path-safety-path-safety-3-11" / ".git" / "config").read_text()
    assert repo == str(tmp_path / "path-safety-path-safety-3-11")


def test_mock_repository_writer_preserves_git_control_directory(tmp_path) -> None:
    marker = "malicious-git-config"
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    config = git_dir / "config"
    config.write_text("safe-config", encoding="utf-8")
    service = AssessmentRepositoryService(github_org="test", github_token="test")

    service._write_repo_files(
        repo,
        {
            "README.md": "safe",
            ".git/config": marker,
        },
    )

    assert (repo / "README.md").read_text() == "safe"
    assert config.read_text() == "safe-config"


def test_mock_repository_replaces_direct_repo_symlink_without_clearing_target(
    tmp_path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "must-remain.txt"
    marker.write_text("preserved", encoding="utf-8")
    mock_root = tmp_path / "mock"
    repo_link = mock_root / "test" / "safe-repo"
    repo_link.parent.mkdir(parents=True)
    repo_link.symlink_to(outside, target_is_directory=True)
    service = AssessmentRepositoryService(github_org="test", github_token="test")
    service.mock_root = mock_root

    repo = service._ensure_mock_repo(
        "safe-repo",
        {"README.md": "safe repository"},
    )

    assert repo == repo_link
    assert not repo.is_symlink()
    assert (repo / "README.md").read_text(encoding="utf-8") == "safe repository"
    assert marker.read_text(encoding="utf-8") == "preserved"
    assert list(outside.iterdir()) == [marker]


def test_task_snapshot_writer_does_not_follow_existing_directory_symlink(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "redirect").symlink_to(outside, target_is_directory=True)

    write_task_repo_files(
        repo,
        {"files": {"redirect/escaped.py": "outside write"}},
    )

    assert not (outside / "escaped.py").exists()


def test_assessment_writer_does_not_follow_existing_directory_symlink(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "redirect").symlink_to(outside, target_is_directory=True)
    service = AssessmentRepositoryService(github_org="test", github_token="test")

    service._write_repo_files(repo, {"redirect/escaped.py": "outside write"})

    assert not (outside / "escaped.py").exists()


def test_repository_names_hash_special_segments_without_collisions() -> None:
    names = {
        raw: task_template_repository_name(SimpleNamespace(task_key=raw))
        for raw in (".", "..", ".git", ".GIT")
    }

    assert all(name.startswith("task-") for name in names.values())
    assert len(set(names.values())) == len(names)
    assert all("/" not in name for name in names.values())
    assert is_safe_repository_name(".git") is False
    assert is_safe_repository_name(".GIT") is False


def test_repository_names_hash_lossy_and_overlong_values_without_collisions() -> None:
    ordinary = task_template_repository_name(SimpleNamespace(task_key="safe_task-1"))
    slash = task_template_repository_name(SimpleNamespace(task_key="same/value"))
    space = task_template_repository_name(SimpleNamespace(task_key="same value"))
    overlong = task_template_repository_name(SimpleNamespace(task_key="a" * 101))

    assert ordinary == "safe_task-1"
    assert slash != space
    assert slash.startswith("same-value-")
    assert space.startswith("same-value-")
    assert all(is_safe_repository_name(value) for value in (slash, space, overlong))


def test_persisted_repository_identity_overrides_mutable_task_key() -> None:
    task = SimpleNamespace(
        id=9,
        organization_id=2,
        task_key="shared-key",
        template_repository_name="task-o2-stable",
    )

    assert task_template_repository_name(task) == "task-o2-stable"
    task.task_key = "renamed-key"
    assert task_template_repository_name(task) == "task-o2-stable"


def test_malformed_persisted_repository_identity_fails_closed() -> None:
    task = SimpleNamespace(
        task_key="otherwise-safe",
        template_repository_name="../another-tenant",
    )
    service = AssessmentRepositoryService(github_org="test", github_token="test")

    with pytest.raises(AssessmentRepositoryError, match="identity is malformed"):
        service.get_template_repo_url(task)


def test_new_tasks_with_colliding_keys_receive_distinct_repository_identities(db) -> None:
    first_org = Organization(name="Repository One", slug="repository-one")
    second_org = Organization(name="Repository Two", slug="repository-two")
    db.add_all([first_org, second_org])
    db.flush()
    first = Task(
        organization_id=first_org.id,
        name="First task",
        task_key="shared-key",
    )
    second = Task(
        organization_id=second_org.id,
        name="Second task",
        task_key="SHARED-KEY",
    )
    db.add_all([first, second])
    db.flush()

    assert first.template_repository_name != second.template_repository_name
    assert first.template_repository_name.startswith(f"task-o{first_org.id}-")
    assert second.template_repository_name.startswith(f"task-o{second_org.id}-")
    assert is_safe_repository_name(first.template_repository_name)
    assert is_safe_repository_name(second.template_repository_name)


def test_colliding_task_keys_cannot_overwrite_another_template_repo(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GITHUB_MOCK_MODE", "true")
    service = AssessmentRepositoryService(github_org="test", github_token="test")
    service.mock_root = tmp_path
    first = SimpleNamespace(
        task_key="shared-key",
        template_repository_name="task-o1-first",
        repo_structure={"files": {"owner.txt": "first"}},
    )
    second = SimpleNamespace(
        task_key="SHARED-KEY",
        template_repository_name="task-o2-second",
        repo_structure={"files": {"owner.txt": "second"}},
    )

    first_url = service.create_template_repo(first)
    second_url = service.create_template_repo(second)
    first.repo_structure = {"files": {"owner.txt": "first updated"}}
    service.create_template_repo(first)

    assert first_url == "mock://test/task-o1-first"
    assert second_url == "mock://test/task-o2-second"
    assert (tmp_path / "test" / "task-o1-first" / "owner.txt").read_text() == (
        "first updated"
    )
    assert (tmp_path / "test" / "task-o2-second" / "owner.txt").read_text() == (
        "second"
    )


def test_local_snapshot_replaces_repo_directory_symlink(monkeypatch, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repo_link = tmp_path / "safe-safe-3-12"
    repo_link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path))
    task = SimpleNamespace(
        id=12,
        organization_id=3,
        task_key="safe",
        name="safe",
        repo_structure={"files": {"README.md": "safe"}},
    )

    repo = Path(recreate_task_main_repo(task))

    assert not repo.is_symlink()
    assert (repo / "README.md").read_text(encoding="utf-8") == "safe"
    assert list(outside.iterdir()) == []


def test_archive_rejects_repo_and_ref_control_path_injection(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_MOCK_MODE", "false")
    service = AssessmentRepositoryService(github_org="test", github_token="test")
    calls = []
    monkeypatch.setattr(service, "_request", lambda *args, **kwargs: calls.append(args))

    bad_repo = service.archive_assessment(
        7,
        repo_url="https://github.com/test/..",
        branch_name="assessment/7",
    )
    bad_branch = service.archive_assessment(
        7,
        repo_url="https://github.com/test/safe",
        branch_name="assessment/7/../../main",
    )

    assert bad_repo["error"] == "missing_repo_url"
    assert bad_branch["error"] == "invalid_branch_name"
    assert calls == []
