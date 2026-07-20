import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.repository_path_safety as path_safety
import app.services.task_repo_service as task_repo_service
from app.services.task_repo_service import (
    UnsafeRepositoryPathError,
    recreate_task_main_repo,
    task_main_repo_path,
    write_repo_file,
)


def _task(repo_structure: dict, *, task_id: int = 71) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        organization_id=9,
        task_key="repo-path-contract",
        name="Repository Path Contract",
        repo_structure=repo_structure,
    )


def test_recreate_task_repo_preserves_normal_relative_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task(
        {
            "files": {
                "README.md": "# Safe task\n",
                "src/main.py": "print('ok')\n",
                "docs/design notes.md": "Useful detail\n",
                "windows\\style\\path.txt": "compatible\n",
                ".gitignore": ".venv/\n",
            }
        }
    )

    repo = Path(recreate_task_main_repo(task))

    assert (repo / "README.md").read_text(encoding="utf-8") == "# Safe task\n"
    assert (repo / "src/main.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (repo / "docs/design notes.md").read_text(
        encoding="utf-8"
    ) == "Useful detail\n"
    assert (repo / "windows/style/path.txt").read_text(
        encoding="utf-8"
    ) == "compatible\n"
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".venv/\n"
    assert (repo / ".git").is_dir()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.py",
        "/absolute/path.py",
        "src/../../outside.py",
        r"C:\Windows\outside.py",
        "src//empty-segment.py",
        ".git/config",
        "src/.GIT/hooks/pre-commit",
        "src/.git./config",
    ],
)
def test_recreate_task_repo_rejects_unsafe_manifest_atomically(
    monkeypatch,
    tmp_path,
    unsafe_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_git_config = (repo / ".git/config").read_bytes()

    task.repo_structure = {
        "files": {
            "would-be-partial.txt": "must not publish\n",
            unsafe_path: "unsafe\n",
        }
    }

    with pytest.raises(UnsafeRepositoryPathError, match="Unsafe repository file path"):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "would-be-partial.txt").exists()
    assert (repo / ".git/config").read_bytes() == original_git_config


def test_repo_writer_rejects_parent_symlink_without_touching_target(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "src").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeRepositoryPathError, match="filesystem target"):
        write_repo_file(repo, "src/escaped.txt", "must stay inside\n")

    assert not (outside / "escaped.txt").exists()


def test_recreate_task_repo_rejects_separator_aliases_before_publish(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    task.repo_structure = {
        "files": {
            "src/main.py": "first\n",
            "src\\main.py": "second\n",
        }
    }

    with pytest.raises(UnsafeRepositoryPathError, match="Duplicate repository file path"):
        recreate_task_main_repo(task)

    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "src").exists()


def test_repo_writer_replaces_final_symlink_instead_of_following_it(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.txt"
    repo.mkdir()
    outside.write_text("outside stays unchanged\n", encoding="utf-8")
    (repo / "result.txt").symlink_to(outside)

    write_repo_file(repo, "result.txt", "safe replacement\n")

    assert outside.read_text(encoding="utf-8") == "outside stays unchanged\n"
    assert not (repo / "result.txt").is_symlink()
    assert (repo / "result.txt").read_text(encoding="utf-8") == "safe replacement\n"


def test_recreate_task_repo_replaces_root_symlink_without_following_it(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("TASK_REPOS_ROOT", str(tmp_path / "repos"))
    task = _task({"files": {"README.md": "fresh repo\n"}})
    repo = Path(task_main_repo_path(task))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not delete\n", encoding="utf-8")
    repo.symlink_to(outside, target_is_directory=True)

    recreated = Path(recreate_task_main_repo(task))

    assert recreated == repo
    assert not recreated.is_symlink()
    assert (recreated / "README.md").read_text(encoding="utf-8") == "fresh repo\n"
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"


def test_recreate_task_repo_restores_prior_snapshot_when_publish_fails(
    monkeypatch,
    tmp_path,
):
    repos_root = tmp_path / "repos"
    monkeypatch.setenv("TASK_REPOS_ROOT", str(repos_root))
    task = _task({"files": {"README.md": "original\n"}})
    repo = Path(recreate_task_main_repo(task))
    original_inode = repo.stat().st_ino
    original_git_config = (repo / ".git/config").read_bytes()
    original_head = (repo / ".git/HEAD").read_bytes()
    task.repo_structure = {"files": {"README.md": "replacement\n"}}

    real_replace = os.replace
    publish_failed = False

    def fail_staging_publish(src, dst, *args, **kwargs):
        nonlocal publish_failed
        if not publish_failed and "-staging-" in str(src) and dst == repo.name:
            publish_failed = True
            raise OSError("injected publish failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(task_repo_service.os, "replace", fail_staging_publish)

    with pytest.raises(OSError, match="injected publish failure"):
        recreate_task_main_repo(task)

    assert publish_failed is True
    assert repo.stat().st_ino == original_inode
    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert (repo / ".git/config").read_bytes() == original_git_config
    assert (repo / ".git/HEAD").read_bytes() == original_head
    assert not list(repos_root.glob(f".{repo.name}-backup-*"))
    assert not list(repos_root.glob(f".{repo.name}-staging-*"))


def test_recreate_task_repo_rejects_symlinked_task_repositories_root(
    monkeypatch,
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not mutate\n", encoding="utf-8")
    root = tmp_path / "repos-link"
    root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("TASK_REPOS_ROOT", str(root))

    with pytest.raises(UnsafeRepositoryPathError, match="root is not a safe"):
        recreate_task_main_repo(_task({"files": {"README.md": "unsafe\n"}}))

    assert sentinel.read_text(encoding="utf-8") == "do not mutate\n"
    assert list(outside.iterdir()) == [sentinel]


def test_repo_writer_cleanup_failure_preserves_error_and_closes_directories(
    monkeypatch,
    tmp_path,
):
    repo = tmp_path / "repo"
    (repo / "nested").mkdir(parents=True)
    real_close = os.close
    closed_descriptors = []

    def fail_replace(*_args, **_kwargs):
        raise OSError("primary replace failure")

    def fail_cleanup_unlink(*_args, **_kwargs):
        raise OSError("cleanup unlink failure")

    def track_close(descriptor):
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(path_safety.os, "replace", fail_replace)
    monkeypatch.setattr(path_safety.os, "unlink", fail_cleanup_unlink)
    monkeypatch.setattr(path_safety.os, "close", track_close)

    with pytest.raises(UnsafeRepositoryPathError) as error:
        path_safety.write_repo_file(repo, "nested/result.txt", "content\n")

    assert str(error.value.__cause__) == "primary replace failure"
    assert len(closed_descriptors) == 2
    for descriptor in closed_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
