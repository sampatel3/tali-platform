from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict

from .repository_path_safety import (
    UnsafeRepositoryPathError,
    canonical_repo_file_path,
    directory_open_flags,
    entry_exists_at,
    remove_entry_at,
    same_open_directory,
    write_repo_file,
)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-.")
    return cleaned or "task"


def _repo_root() -> Path:
    root = Path(os.getenv("TASK_REPOS_ROOT", "/tmp/taali_task_repos"))
    root.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(root, directory_open_flags())
    except OSError as exc:
        raise UnsafeRepositoryPathError(
            f"Task repository root is not a safe directory: {root}"
        ) from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise UnsafeRepositoryPathError(
                f"Task repository root is not a directory: {root}"
            )
    finally:
        os.close(descriptor)
    return root


def _canonical_repo_files(repo_structure: Dict[str, Any] | None) -> Dict[str, str]:
    """Validate a whole manifest before performing its first filesystem write."""

    canonical: Dict[str, str] = {}
    source_paths: Dict[str, str] = {}
    for raw_path, content in normalize_repo_files(repo_structure).items():
        path = canonical_repo_file_path(raw_path)
        previous = source_paths.get(path)
        if previous is not None:
            raise UnsafeRepositoryPathError(
                f"Duplicate repository file path: {previous!r} and {raw_path!r}"
            )
        source_paths[path] = raw_path
        canonical[path] = content
    return canonical


def normalize_repo_file_content(content: Any) -> str:
    if not isinstance(content, str):
        return str(content)

    if "\n" in content or "\r" in content:
        return content

    if not any(token in content for token in ("\\n", "\\r", "\\t", "\\'", '\\"', "\\\\")):
        return content

    try:
        decoded = bytes(content, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return content
    return decoded if decoded else content


def normalize_repo_files(repo_structure: Dict[str, Any] | None) -> Dict[str, str]:
    files = (repo_structure or {}).get("files") or {}
    if isinstance(files, list):
        normalized = {}
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if not path:
                continue
            normalized[path] = normalize_repo_file_content(entry.get("content", ""))
        files = normalized

    if not isinstance(files, dict):
        return {}

    normalized_files: Dict[str, str] = {}
    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not rel_path.strip():
            continue
        normalized_files[rel_path] = normalize_repo_file_content(content)
    return normalized_files


def repo_file_count(repo_structure: Dict[str, Any] | None) -> int:
    return len(normalize_repo_files(repo_structure))


def build_default_repo_structure(
    starter_code: str | None,
    test_code: str | None,
    *,
    task_name: str | None = None,
    scenario: str | None = None,
) -> Dict[str, Any]:
    name = task_name or "Assessment Task"
    intro = (scenario or "").strip()
    readme_lines = [f"# {name}", ""]
    if intro:
        readme_lines.extend([intro, ""])
    readme_lines.extend(
        [
            "## Files",
            "- `src/task.py`: starter implementation for candidates",
        ]
    )
    if test_code:
        readme_lines.append("- `tests/test_task.py`: pytest suite used for evaluation")
    readme_lines.append("")
    files = {
        "README.md": "\n".join(readme_lines),
        "src/task.py": starter_code or "# Starter code\n",
    }
    if test_code:
        files["tests/test_task.py"] = test_code
    return {
        "name": _slug(name),
        "files": files,
    }


def _write_repo_files(
    repo_dir: Path,
    repo_structure: Dict[str, Any] | None,
    *,
    repo_fd: int | None = None,
) -> None:
    files = _canonical_repo_files(repo_structure)
    if not files:
        return

    for rel_path, content in files.items():
        write_repo_file(repo_dir, rel_path, content, repo_fd=repo_fd)


def _task_repo_dir_name(task: Any) -> str:
    key = getattr(task, "task_key", None) or f"task-{getattr(task, 'id', 'unknown')}"
    name = getattr(task, "name", None) or "assessment-task"
    task_id = getattr(task, "id", None)
    org_id = getattr(task, "organization_id", None)
    identity = "-".join(
        str(part) for part in (org_id, task_id) if part is not None
    ) or "x"
    return f"{_slug(key)}-{_slug(name)}-{_slug(identity)}"


def task_main_repo_path(task: Any) -> str:
    # Two tasks (possibly in different orgs) can share the same key+name —
    # qualify the directory with the task id and org id so distinct tasks
    # never share a snapshot dir and recreate_task_main_repo can't rmtree
    # another task's repo out from under it.
    repo_dir = _repo_root() / _task_repo_dir_name(task)
    return str(repo_dir)


def recreate_task_main_repo(task: Any) -> str:
    """Recreate the canonical `main` repo snapshot for a task.

    Returns absolute path to the recreated repo directory.
    """
    repo_root = _repo_root()
    repo_name = _task_repo_dir_name(task)
    repo_dir = repo_root / repo_name
    root_fd = os.open(repo_root, directory_open_flags())
    staging_name = f".{repo_name}-staging-{secrets.token_hex(16)}"
    backup_name: str | None = None
    staging_fd: int | None = None
    try:
        os.mkdir(staging_name, mode=0o700, dir_fd=root_fd)
        staging_fd = os.open(staging_name, directory_open_flags(), dir_fd=root_fd)
        staging_dir = repo_root / staging_name
        _write_repo_files(
            staging_dir,
            getattr(task, "repo_structure", None),
            repo_fd=staging_fd,
        )

        if not same_open_directory(staging_dir, staging_fd):
            raise UnsafeRepositoryPathError("Task repository staging path changed")

        # Best-effort git init to make this a real canonical repo snapshot.
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=staging_dir,
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=staging_dir,
            check=False,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=TAALI",
                "-c",
                "user.email=noreply@taali.ai",
                "commit",
                "-m",
                "Initialize task repo",
            ],
            cwd=staging_dir,
            check=False,
            capture_output=True,
        )

        if not same_open_directory(staging_dir, staging_fd):
            raise UnsafeRepositoryPathError("Task repository staging path changed")

        os.close(staging_fd)
        staging_fd = None
        if entry_exists_at(root_fd, repo_name):
            backup_name = f".{repo_name}-backup-{secrets.token_hex(16)}"
            os.replace(
                repo_name,
                backup_name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
        try:
            os.replace(
                staging_name,
                repo_name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
            staging_name = ""
        except BaseException:
            if backup_name is not None:
                try:
                    os.replace(
                        backup_name,
                        repo_name,
                        src_dir_fd=root_fd,
                        dst_dir_fd=root_fd,
                    )
                    backup_name = None
                except OSError as restore_exc:
                    raise UnsafeRepositoryPathError(
                        "Task repository publish failed and the prior snapshot "
                        f"remains at {backup_name!r}"
                    ) from restore_exc
            raise

        if backup_name is not None:
            try:
                remove_entry_at(root_fd, backup_name)
                backup_name = None
            except OSError:
                pass
    finally:
        if staging_fd is not None:
            try:
                os.close(staging_fd)
            except OSError:
                pass
        try:
            if staging_name and entry_exists_at(root_fd, staging_name):
                try:
                    remove_entry_at(root_fd, staging_name)
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            try:
                os.close(root_fd)
            except OSError:
                pass

    return str(repo_dir)
