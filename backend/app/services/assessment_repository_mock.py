"""Descriptor-anchored local Git repository operations for assessments."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from .assessment_repository_types import AssessmentRepositoryError
from .repository_path_safety import (
    UnsafeRepositoryPathError,
    canonical_repo_file_path,
    is_safe_repository_segment,
    pinned_subdirectory,
    run_in_pinned_directory,
    same_open_directory,
    validate_candidate_workspace_root,
    validate_manifest_file_hierarchy,
)
from .task_catalog import task_workspace_root_name
from .task_repo_service import normalize_repo_files


def sanitize_candidate_workspace_files(
    repo_structure: Dict[str, Any] | None,
    *,
    workspace_root: str | None = None,
) -> Dict[str, str]:
    """Return a canonical, traversal-safe candidate repository manifest."""

    if workspace_root is None:
        workspace_root = (
            f"/workspace/{task_workspace_root_name({'repo_structure': repo_structure})}"
        )
    try:
        workspace_root = validate_candidate_workspace_root(workspace_root)
    except UnsafeRepositoryPathError as exc:
        raise AssessmentRepositoryError(str(exc)) from exc

    sanitized: Dict[str, str] = {}
    source_paths: Dict[str, str] = {}
    for raw_path, content in normalize_repo_files(repo_structure).items():
        try:
            canonical = canonical_repo_file_path(
                raw_path,
                workspace_root=workspace_root,
            )
        except UnsafeRepositoryPathError as exc:
            raise AssessmentRepositoryError(
                f"Unsafe candidate workspace path: {raw_path!r}"
            ) from exc
        previous = source_paths.get(canonical)
        if previous is not None:
            raise AssessmentRepositoryError(
                "Unsafe candidate workspace duplicate path: "
                f"{previous!r} and {raw_path!r}"
            )
        source_paths[canonical] = raw_path
        sanitized[canonical] = content
    try:
        validate_manifest_file_hierarchy(source_paths)
    except UnsafeRepositoryPathError as exc:
        raise AssessmentRepositoryError(
            f"Unsafe candidate workspace manifest: {exc}"
        ) from exc
    return sanitized


class AssessmentRepositoryMockMixin:
    """Local mock transport that never resolves a mutable cwd pathname."""

    mock_root: Path
    github_org: str

    def _run_pinned(
        self,
        args: list[str],
        repo_fd: int,
    ) -> subprocess.CompletedProcess[str]:
        return run_in_pinned_directory(
            args,
            repo_fd,
            check=False,
            capture_output=True,
            text=True,
        )

    def _run_strict_pinned(
        self,
        args: list[str],
        repo_fd: int,
        context: str,
    ) -> subprocess.CompletedProcess[str]:
        result = self._run_pinned(args, repo_fd)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-500:]
            raise AssessmentRepositoryError(
                f"{context} failed ({result.returncode}): {detail}"
            )
        return result

    @contextmanager
    def _pinned_mock_repository(
        self,
        repo_name: str,
    ) -> Iterator[tuple[Path, int]]:
        if not is_safe_repository_segment(repo_name):
            raise AssessmentRepositoryError("Task repository name is unsafe")
        try:
            with pinned_subdirectory(
                self.mock_root,
                (self.github_org, repo_name),
            ) as pinned:
                yield pinned
        except UnsafeRepositoryPathError as exc:
            raise AssessmentRepositoryError(
                "Local mock repository path is unsafe"
            ) from exc

    def _ensure_mock_repo(self, repo_name: str, files: Dict[str, str]) -> Path:
        safe_files = sanitize_candidate_workspace_files({"files": files})
        with self._pinned_mock_repository(repo_name) as (repo, repo_fd):
            return self._sync_mock_repo(repo, repo_fd, safe_files)

    def _sync_mock_repo(
        self,
        repo: Path,
        repo_fd: int,
        files: Dict[str, str],
    ) -> Path:
        if not same_open_directory(repo, repo_fd):
            raise AssessmentRepositoryError("Local repository path changed")
        self._run_pinned(["git", "init", "-b", "main"], repo_fd)
        if not same_open_directory(repo, repo_fd):
            raise AssessmentRepositoryError("Local repository path changed")

        # Mock repos are long-lived across assessment branches. Always restore
        # main before syncing files; a repeated git init does not switch branch.
        has_main = self._run_pinned(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/main"],
            repo_fd,
        ).returncode == 0
        if has_main:
            self._run_strict_pinned(
                ["git", "checkout", "-f", "main"],
                repo_fd,
                "checkout mock main",
            )
        else:
            has_head = self._run_pinned(
                ["git", "rev-parse", "--verify", "HEAD"],
                repo_fd,
            ).returncode == 0
            if has_head:
                self._run_strict_pinned(
                    ["git", "branch", "-f", "main", "HEAD"],
                    repo_fd,
                    "create mock main",
                )
                self._run_strict_pinned(
                    ["git", "checkout", "-f", "main"],
                    repo_fd,
                    "checkout mock main",
                )
            else:
                self._run_strict_pinned(
                    ["git", "checkout", "-B", "main"],
                    repo_fd,
                    "initialize mock main",
                )

        if not same_open_directory(repo, repo_fd):
            raise AssessmentRepositoryError("Local repository path changed")
        self._clear_worktree(repo, repo_fd=repo_fd)
        self._write_repo_files(repo, files, repo_fd=repo_fd)
        if not same_open_directory(repo, repo_fd):
            raise AssessmentRepositoryError("Local repository path changed")
        self._run_strict_pinned(
            ["git", "add", "-A"],
            repo_fd,
            "stage mock template files",
        )
        if not same_open_directory(repo, repo_fd):
            raise AssessmentRepositoryError("Local repository path changed")
        commit = self._run_pinned(
            [
                "git",
                "-c",
                "user.email=taali@local",
                "-c",
                "user.name=TAALI",
                "commit",
                "-m",
                "Initialize task template",
            ],
            repo_fd,
        )
        commit_output = f"{commit.stdout or ''}\n{commit.stderr or ''}".lower()
        if commit.returncode != 0 and "nothing to commit" not in commit_output:
            detail = (commit.stderr or commit.stdout or "").strip()[-500:]
            raise AssessmentRepositoryError(
                f"Mock template commit failed: {detail}"
            )
        if not same_open_directory(repo, repo_fd):
            raise AssessmentRepositoryError("Local repository path changed")
        return repo
