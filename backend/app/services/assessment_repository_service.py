from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

import httpx


class AssessmentRepositoryError(RuntimeError):
    """Raised when repository provisioning fails."""


@dataclass
class BranchContext:
    repo_url: str
    branch_name: str
    clone_command: str


class AssessmentRepositoryService:
    """GitHub repo/branch manager with local mock harness for tests/dev."""

    def __init__(self, github_org: str | None = None, github_token: str | None = None):
        self.github_org = github_org or os.getenv("GITHUB_ORG", "taali-assessments")
        self.github_token = github_token or os.getenv("GITHUB_TOKEN", "")
        self.mock_mode = os.getenv("GITHUB_MOCK_MODE", "true").lower() in {"1", "true", "yes"}
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
        files = (repo_structure or {}).get("files") or {}
        out: Dict[str, str] = {}
        if isinstance(files, dict):
            for p, c in files.items():
                out[str(p)] = c if isinstance(c, str) else str(c)
        elif isinstance(files, list):
            for entry in files:
                if isinstance(entry, dict) and (entry.get("path") or entry.get("name")):
                    out[str(entry.get("path") or entry.get("name"))] = str(entry.get("content", ""))
        return out

    def _run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)

    def _run_strict(self, args: list[str], cwd: Path, context: str) -> subprocess.CompletedProcess[str]:
        result = self._run(args, cwd=cwd)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-500:]
            raise AssessmentRepositoryError(f"{context} failed ({result.returncode}): {detail}")
        return result

    def _require_token(self) -> str:
        token = (self.github_token or "").strip()
        if not token:
            raise AssessmentRepositoryError("GITHUB_TOKEN is required when GITHUB_MOCK_MODE is false")
        return token

    def _headers(self) -> Dict[str, str]:
        token = self._require_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Dict[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        url = f"{self.api_base}{path}"
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers(),
                json=json_payload,
                timeout=self.http_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise AssessmentRepositoryError(f"GitHub API request failed for {path}: {exc}") from exc

        if response.status_code not in expected_statuses:
            detail = response.text.strip()[:500]
            raise AssessmentRepositoryError(
                f"GitHub API {method} {path} returned {response.status_code}: {detail}"
            )
        return response

    def _ensure_repo_exists(self, repo_name: str) -> None:
        check = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}",
            expected_statuses=(200, 404),
        )
        if check.status_code == 200:
            return

        create = self._request(
            "POST",
            f"/orgs/{self.github_org}/repos",
            json_payload={
                "name": repo_name,
                "private": True,
                "auto_init": False,
                "has_issues": False,
                "has_wiki": False,
                "has_projects": False,
            },
            expected_statuses=(201, 422),
        )
        if create.status_code == 201:
            return

        # 422 can happen due to existing repo in races/retries; verify and continue if present.
        verify = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}",
            expected_statuses=(200, 404),
        )
        if verify.status_code == 200:
            return
        detail = create.text.strip()[:500]
        raise AssessmentRepositoryError(f"Unable to create repository {repo_name}: {detail}")

    def _ensure_mock_repo(self, repo_name: str, files: Dict[str, str]) -> Path:
        repo = self.mock_root / self.github_org / repo_name
        repo.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self._run(["git", "init", "-b", "main"], repo)
        self._run(["git", "add", "."], repo)
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

    def _main_head_sha(self, repo_name: str) -> str:
        ref = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}/git/ref/heads/main",
            expected_statuses=(200, 404),
        )
        if ref.status_code == 404:
            raise AssessmentRepositoryError(f"Repository {repo_name} has no main branch")
        payload = ref.json() if ref.content else {}
        sha = (payload.get("object") or {}).get("sha")
        if not sha:
            raise AssessmentRepositoryError(f"Unable to resolve main branch SHA for {repo_name}")
        return str(sha)

    @staticmethod
    def _response_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text or ""
        message = payload.get("message")
        if isinstance(message, str):
            return message
        return str(payload)

    def create_template_repo(self, task: Any) -> str:
        repo_name = self._repo_name(task)
        files = self._repo_files(task)
        if self.mock_mode:
            self._ensure_mock_repo(repo_name, files)
            return self.get_template_repo_url(task)
        self._ensure_repo_exists(repo_name)
        self._sync_repo_main_branch(repo_name, files)
        return self.get_template_repo_url(task)

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
            # Handle existing branch safely by suffixing
            existing = subprocess.run(["git", "branch", "--list", branch_name], cwd=repo, capture_output=True, text=True)
            if existing.stdout.strip():
                suffix = 1
                while True:
                    candidate = f"{branch_name}-{suffix}"
                    chk = subprocess.run(["git", "branch", "--list", candidate], cwd=repo, capture_output=True, text=True)
                    if not chk.stdout.strip():
                        branch_name = candidate
                        break
                    suffix += 1
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

    def archive_assessment(self, assessment_id: int) -> Dict[str, Any]:
        return {"assessment_id": assessment_id, "archived": True, "mode": "mock" if self.mock_mode else "github"}
