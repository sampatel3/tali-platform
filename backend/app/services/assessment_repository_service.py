from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from ..platform.config import settings
from .assessment_repository_github import AssessmentRepositoryGitHubMixin
from .assessment_repository_types import AssessmentRepositoryError, BranchContext
from .task_repo_service import normalize_repo_files


class AssessmentRepositoryService(AssessmentRepositoryGitHubMixin):
    """GitHub repo/branch manager with local mock harness for tests/dev."""

    def __init__(self, github_org: str | None = None, github_token: str | None = None):
        self.github_org = github_org or os.getenv("GITHUB_ORG", "taali-assessments")
        self.github_token = github_token or os.getenv("GITHUB_TOKEN", "")
        mock_mode_env = os.getenv("GITHUB_MOCK_MODE")
        if mock_mode_env is None:
            self.mock_mode = bool(getattr(settings, "GITHUB_MOCK_MODE", False))
        else:
            self.mock_mode = mock_mode_env.lower() in {"1", "true", "yes"}
        self.mock_root = Path(os.getenv("GITHUB_MOCK_ROOT", "/tmp/taali_github_mock"))
        self.api_base = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
        self.clone_base = os.getenv("GITHUB_CLONE_BASE_URL", "https://github.com").rstrip("/")
        try:
            self.http_timeout_seconds = float(os.getenv("GITHUB_HTTP_TIMEOUT_SECONDS", "20"))
        except ValueError:
            self.http_timeout_seconds = 20.0

    def _repo_name(self, task: Any) -> str:
        raw = (
            getattr(task, "task_key", None)
            or (task.get("task_id") if isinstance(task, dict) else None)
            or getattr(task, "id", "task")
        )
        return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(raw)).strip("-").lower() or "task"

    def _repo_files(self, task: Any) -> Dict[str, str]:
        repo_structure = getattr(task, "repo_structure", None) if not isinstance(task, dict) else task.get("repo_structure")
        return normalize_repo_files(repo_structure)

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)

    def _run_strict(self, args: list[str], cwd: Path, context: str) -> subprocess.CompletedProcess[str]:
        result = self._run(args, cwd=cwd)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-500:]
            raise AssessmentRepositoryError(f"{context} failed ({result.returncode}): {detail}")
        return result

    def _ensure_mock_repo(self, repo_name: str, files: Dict[str, str]) -> Path:
        repo = self.mock_root / self.github_org / repo_name
        repo.mkdir(parents=True, exist_ok=True)
        self._run(["git", "init", "-b", "main"], repo)

        # Mock repos are long-lived across assessment branches. Always restore
        # the template branch before syncing task files; merely re-running
        # ``git init -b main`` does not switch an existing repository away from
        # its last candidate branch, and legacy empty repos may have no main ref.
        has_main = self._run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/main"], repo
        ).returncode == 0
        if has_main:
            self._run_strict(["git", "checkout", "-f", "main"], repo, "checkout mock main")
        else:
            has_head = self._run(
                ["git", "rev-parse", "--verify", "HEAD"], repo
            ).returncode == 0
            if has_head:
                self._run_strict(
                    ["git", "branch", "-f", "main", "HEAD"],
                    repo,
                    "create mock main",
                )
                self._run_strict(
                    ["git", "checkout", "-f", "main"], repo, "checkout mock main"
                )
            else:
                self._run_strict(
                    ["git", "checkout", "-B", "main"], repo, "initialize mock main"
                )

        self._clear_worktree(repo)
        self._write_repo_files(repo, files)
        self._run_strict(["git", "add", "-A"], repo, "stage mock template files")
        commit = self._run(
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
            repo,
        )
        commit_output = f"{commit.stdout or ''}\n{commit.stderr or ''}".lower()
        if commit.returncode != 0 and "nothing to commit" not in commit_output:
            detail = (commit.stderr or commit.stdout or "").strip()[-500:]
            raise AssessmentRepositoryError(f"Mock template commit failed: {detail}")
        return repo

    def _clear_worktree(self, repo: Path) -> None:
        for entry in repo.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)

    def _write_repo_files(self, repo: Path, files: Dict[str, str]) -> None:
        normalized = files or {}
        if not normalized:
            normalized = {"README.md": "# Assessment task\n"}
        for rel, content in normalized.items():
            safe_rel = str(rel).replace("\\", "/").lstrip("/")
            if not safe_rel or ".." in Path(safe_rel).parts:
                continue
            target = repo / safe_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")

    def _authenticated_repo_url(self, repo_url: str) -> str:
        if self.mock_mode or not repo_url.startswith("https://"):
            return repo_url
        token = (self.github_token or "").strip()
        if not token:
            return repo_url
        if "@" in repo_url.split("://", 1)[1].split("/", 1)[0]:
            return repo_url
        quoted = quote(token, safe="")
        return repo_url.replace("https://", f"https://x-access-token:{quoted}@", 1)

    def _sync_repo_main_branch(self, repo_name: str, files: Dict[str, str]) -> None:
        repo_url = self.get_template_repo_url_by_name(repo_name)
        auth_repo_url = self._authenticated_repo_url(repo_url)

        with tempfile.TemporaryDirectory(prefix="taali-repo-sync-") as tmp:
            repo = Path(tmp) / "repo"
            self._run_strict(["git", "clone", auth_repo_url, str(repo)], Path(tmp), "clone template repo")
            self._run_strict(["git", "checkout", "-B", "main"], repo, "checkout main")
            self._clear_worktree(repo)
            self._write_repo_files(repo, files)

            self._run_strict(["git", "add", "-A"], repo, "stage template files")
            staged_diff = self._run(["git", "diff", "--cached", "--quiet"], repo)
            has_changes = staged_diff.returncode == 1
            has_head = self._run(["git", "rev-parse", "--verify", "HEAD"], repo).returncode == 0
            if has_changes or not has_head:
                self._run_strict(
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
                    repo,
                    "commit template files",
                )
            self._run_strict(["git", "push", "-u", "origin", "main"], repo, "push main branch")

        # Best effort: keep repository default branch pinned to main.
        self._request(
            "PATCH",
            f"/repos/{self.github_org}/{repo_name}",
            json_payload={"default_branch": "main"},
            expected_statuses=(200, 422),
        )

    # Digest stamped into the repo *description* after each sync, so a later send
    # can tell "main already holds these files" with one GET instead of a full
    # clone+rewrite+push (the dominant, per-org-serialized cost of a send).
    _TEMPLATE_HASH_PREFIX = "taali-template-sha1:"

    @staticmethod
    def _files_digest(files: Dict[str, str]) -> str:
        norm = {str(k): ("" if v is None else str(v)) for k, v in (files or {}).items()}
        blob = json.dumps(norm, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()  # noqa: S324 - cache key

    def _template_is_current(self, repo_name: str, files: Dict[str, str]) -> bool:
        """True when the repo's description stamps this digest (main already holds
        these files). Any uncertainty -> False, so we fall back to a full sync."""
        try:
            resp = self._request("GET", f"/repos/{self.github_org}/{repo_name}", expected_statuses=(200, 404))
            desc = (resp.json() or {}).get("description") if resp.status_code == 200 else None
        except (AssessmentRepositoryError, ValueError):
            return False
        return str(desc or "").strip() == f"{self._TEMPLATE_HASH_PREFIX}{self._files_digest(files)}"

    def _stamp_template_hash(self, repo_name: str, files: Dict[str, str]) -> None:
        """Stamp the synced digest so the next send can skip the clone+push. Best-effort."""
        try:
            self._request(
                "PATCH", f"/repos/{self.github_org}/{repo_name}",
                json_payload={"description": f"{self._TEMPLATE_HASH_PREFIX}{self._files_digest(files)}"},
                expected_statuses=(200, 422),
            )
        except AssessmentRepositoryError:
            pass

    def create_template_repo(self, task: Any, *, force: bool = False) -> str:
        repo_name = self._repo_name(task)
        files = self._repo_files(task)
        if self.mock_mode:
            self._ensure_mock_repo(repo_name, files)
            return self.get_template_repo_url(task)
        # Fast path: main is identical across every assessment of a task, so skip
        # the clone+rewrite+push when it already holds these files. force=True
        # (admin resync) always re-pushes; the first send (no stamp) still syncs.
        if not force and self._template_is_current(repo_name, files):
            return self.get_template_repo_url(task)
        self._ensure_repo_exists(repo_name)
        self._sync_repo_main_branch(repo_name, files)
        self._stamp_template_hash(repo_name, files)
        return self.get_template_repo_url(task)

    def verify_template_repo(self, task: Any) -> str:
        """Prove that ``task`` has a usable template repository and ``main``.

        This is deliberately task-specific.  A successful generic GitHub token
        probe says nothing about whether the repository a future assessment
        branch will be created from actually exists.  Approval and production
        Turn-on readiness call this method so an active task can never be only a
        database flag backed by a missing repository.

        Returns the clone URL on success and raises
        :class:`AssessmentRepositoryError` on any uncertainty.
        """
        repo_name = self._repo_name(task)
        files = self._repo_files(task)
        if not files:
            raise AssessmentRepositoryError(
                f"Task {getattr(task, 'id', repo_name)} has no repository files"
            )

        if self.mock_mode:
            repo = self.mock_root / self.github_org / repo_name
            if not repo.is_dir() or not (repo / ".git").is_dir():
                raise AssessmentRepositoryError(
                    f"Mock template repository {repo_name} does not exist"
                )
            main = self._run(["git", "rev-parse", "--verify", "refs/heads/main"], repo)
            if main.returncode != 0 or not (main.stdout or "").strip():
                detail = (main.stderr or main.stdout or "").strip()[-500:]
                raise AssessmentRepositoryError(
                    f"Mock template repository {repo_name} has no main branch: {detail}"
                )
            return self.get_template_repo_url_by_name(repo_name)

        repo = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}",
            expected_statuses=(200, 404),
        )
        if repo.status_code != 200:
            raise AssessmentRepositoryError(
                f"Template repository {self.github_org}/{repo_name} does not exist"
            )
        # The exact downstream branch path resolves main in the same way.  This
        # catches an empty/half-created repo even when the repository GET passes.
        self._main_head_sha(repo_name)
        return self.get_template_repo_url_by_name(repo_name)

    def get_template_repo_url(self, task: Any) -> str:
        repo_name = self._repo_name(task)
        return self.get_template_repo_url_by_name(repo_name)

    def get_template_repo_url_by_name(self, repo_name: str) -> str:
        if self.mock_mode:
            return f"mock://{self.github_org}/{repo_name}"
        return f"{self.clone_base}/{self.github_org}/{repo_name}.git"

    def authenticated_repo_url(self, repo_url: str) -> str:
        return self._authenticated_repo_url(repo_url)

    def create_assessment_branch(self, task: Any, assessment_id: int) -> BranchContext:
        repo_name = self._repo_name(task)
        branch_name = f"assessment/{assessment_id}"
        if self.mock_mode:
            repo = self._ensure_mock_repo(repo_name, self._repo_files(task))
            # Enumerate refs once, then resolve collisions in memory. Test and
            # development databases commonly reuse assessment IDs; probing one
            # suffix per Git subprocess made repeated runs progressively slower
            # (thousands of historical branches could block one request for
            # tens of seconds).
            refs = self._run_strict(
                ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
                repo,
                "list mock branches",
            )
            existing_branches = {
                line.strip() for line in (refs.stdout or "").splitlines() if line.strip()
            }
            if branch_name in existing_branches:
                suffix = 1
                while f"{branch_name}-{suffix}" in existing_branches:
                    suffix += 1
                branch_name = f"{branch_name}-{suffix}"
            self._run(["git", "checkout", "main"], repo)
            self._run(["git", "checkout", "-b", branch_name], repo)
            repo_url = f"mock://{self.github_org}/{repo_name}"
            return BranchContext(repo_url=repo_url, branch_name=branch_name, clone_command=f"git clone --branch {branch_name} {repo_url}")

        self.create_template_repo(task)
        main_sha = self._main_head_sha(repo_name)

        selected_branch = branch_name
        for suffix in range(0, 1000):
            candidate = branch_name if suffix == 0 else f"{branch_name}-{suffix}"
            create = self._request(
                "POST",
                f"/repos/{self.github_org}/{repo_name}/git/refs",
                json_payload={"ref": f"refs/heads/{candidate}", "sha": main_sha},
                expected_statuses=(201, 422),
            )
            if create.status_code == 201:
                selected_branch = candidate
                break

            msg = self._response_message(create).lower()
            if "reference already exists" in msg or "already exists" in msg:
                continue
            raise AssessmentRepositoryError(
                f"Failed to create branch {candidate} in {repo_name}: {self._response_message(create)}"
            )
        else:
            raise AssessmentRepositoryError(f"Unable to allocate unique branch name for assessment {assessment_id}")

        repo_url = self.get_template_repo_url_by_name(repo_name)
        return BranchContext(
            repo_url=repo_url,
            branch_name=selected_branch,
            clone_command=f"git clone --branch {selected_branch} {repo_url}",
        )

    def archive_assessment(
        self,
        assessment_id: int,
        *,
        repo_url: str | None = None,
        branch_name: str | None = None,
    ) -> Dict[str, Any]:
        """Delete the candidate's assessment branch on GitHub.

        The branch is the only assessment-specific artefact we ever create on
        a task repo (templates live on `main` and are reused). Deleting it
        keeps the task repo clean and avoids unbounded ref growth. Caller is
        responsible for first persisting any state they want to keep
        (`git_evidence`, `final_repo_state` SHA, code_snapshots).
        """
        result: Dict[str, Any] = {
            "assessment_id": assessment_id,
            "archived": False,
            "mode": "mock" if self.mock_mode else "github",
        }

        if not branch_name:
            result["error"] = "missing_branch_name"
            return result

        if self.mock_mode:
            # Local mock branches are throwaway; nothing to delete.
            result["archived"] = True
            return result

        repo_name = self._repo_name_from_url(repo_url) if repo_url else None
        if not repo_name:
            result["error"] = "missing_repo_url"
            return result

        try:
            response = self._request(
                "DELETE",
                f"/repos/{self.github_org}/{repo_name}/git/refs/heads/{branch_name}",
                expected_statuses=(204, 404, 422),
            )
        except AssessmentRepositoryError as exc:
            result["error"] = str(exc)
            return result

        if response.status_code in (204, 404):
            # 204 = deleted, 404 = already gone — both are success for our purposes.
            result["archived"] = True
            result["branch_deleted"] = response.status_code == 204
        else:
            result["error"] = self._response_message(response) or f"http_{response.status_code}"
        return result

    @staticmethod
    def _repo_name_from_url(repo_url: str | None) -> str | None:
        if not repo_url:
            return None
        candidate = str(repo_url).rstrip("/")
        if candidate.endswith(".git"):
            candidate = candidate[:-4]
        if "://" in candidate:
            candidate = candidate.split("://", 1)[1]
        # Strip the host + org prefix, keeping the final path segment.
        name = candidate.rsplit("/", 1)[-1]
        return name or None
