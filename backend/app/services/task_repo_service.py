from __future__ import annotations

import fcntl
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any, Dict

from .repository_path_safety import (
    UnsafeRepositoryPathError,
    canonical_repo_file_path,
    directory_open_flags,
    entry_exists_at,
    remove_entry_at,
    run_in_pinned_directory,
    same_open_directory,
    validate_manifest_file_hierarchy,
    write_repo_file,
)
from .task_repo_publication import (
    _acquire_publication_lock,
    _migrate_legacy_transaction_remnants,
    _open_transaction_directory,
    _recover_interrupted_publication,
    _transaction_remnants,
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
    validate_manifest_file_hierarchy(source_paths)
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
    _write_canonical_repo_files(repo_dir, files, repo_fd=repo_fd)


def _write_canonical_repo_files(
    repo_dir: Path,
    files: Dict[str, str],
    *,
    repo_fd: int | None = None,
) -> None:
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
    # Reject the entire manifest before creating a root or staging directory.
    # This preserves the last published snapshot on every validation failure.
    repo_files = _canonical_repo_files(getattr(task, "repo_structure", None))
    repo_root = _repo_root()
    repo_name = _task_repo_dir_name(task)
    repo_dir = repo_root / repo_name
    root_fd = os.open(repo_root, directory_open_flags())
    transaction_dir: Path | None = None
    transaction_fd: int | None = None
    lock_fd: int | None = None
    staging_name = f"staging-{secrets.token_hex(16)}"
    backup_name: str | None = None
    staging_fd: int | None = None
    try:
        transaction_dir, transaction_fd = _open_transaction_directory(
            root_fd,
            repo_root,
            repo_name,
        )
        lock_fd = _acquire_publication_lock(transaction_fd)
        _migrate_legacy_transaction_remnants(
            root_fd,
            transaction_fd,
            repo_name,
        )
        _recover_interrupted_publication(
            root_fd,
            transaction_fd,
            repo_root,
            repo_name,
        )
        os.mkdir(staging_name, mode=0o700, dir_fd=transaction_fd)
        staging_fd = os.open(
            staging_name,
            directory_open_flags(),
            dir_fd=transaction_fd,
        )
        staging_dir = transaction_dir / staging_name
        _write_canonical_repo_files(
            staging_dir,
            repo_files,
            repo_fd=staging_fd,
        )

        if not same_open_directory(staging_dir, staging_fd):
            raise UnsafeRepositoryPathError("Task repository staging path changed")

        # Best-effort git init to make this a real canonical repo snapshot.
        run_in_pinned_directory(
            ["git", "init", "-b", "main"],
            staging_fd,
            check=False,
            capture_output=True,
        )
        if not same_open_directory(staging_dir, staging_fd):
            raise UnsafeRepositoryPathError("Task repository staging path changed")
        run_in_pinned_directory(
            ["git", "add", "."],
            staging_fd,
            check=False,
            capture_output=True,
        )
        if not same_open_directory(staging_dir, staging_fd):
            raise UnsafeRepositoryPathError("Task repository staging path changed")
        run_in_pinned_directory(
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
            staging_fd,
            check=False,
            capture_output=True,
        )

        if not same_open_directory(staging_dir, staging_fd):
            raise UnsafeRepositoryPathError("Task repository staging path changed")

        os.close(staging_fd)
        staging_fd = None
        if entry_exists_at(root_fd, repo_name):
            backup_name = f"backup-{secrets.token_hex(16)}"
            os.replace(
                repo_name,
                backup_name,
                src_dir_fd=root_fd,
                dst_dir_fd=transaction_fd,
            )
        try:
            os.replace(
                staging_name,
                repo_name,
                src_dir_fd=transaction_fd,
                dst_dir_fd=root_fd,
            )
            staging_name = ""
        except BaseException:
            if backup_name is not None:
                try:
                    os.replace(
                        backup_name,
                        repo_name,
                        src_dir_fd=transaction_fd,
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
                remove_entry_at(transaction_fd, backup_name)
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
            if transaction_fd is not None and lock_fd is not None:
                for abandoned_staging in _transaction_remnants(
                    transaction_fd,
                    "staging-",
                ):
                    try:
                        remove_entry_at(transaction_fd, abandoned_staging)
                    except OSError:
                        pass
        except OSError:
            pass
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
            if transaction_fd is not None:
                try:
                    os.close(transaction_fd)
                except OSError:
                    pass
            try:
                os.close(root_fd)
            except OSError:
                pass

    return str(repo_dir)
