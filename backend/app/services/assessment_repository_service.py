from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class BranchContext:
    repo_url: str
    branch_name: str
    clone_command: str


class AssessmentRepositoryService:
    """GitHub repo/branch manager with local mock harness for tests/dev.

    Production: Set GITHUB_MOCK_MODE=false and implement real GitHub API in
    create_template_repo / create_assessment_branch (e.g. PyGithub or REST:
    create repo in org, push files to main, create branch assessment/{id}, return
    clone URL). Mock mode uses local git repos under GITHUB_MOCK_ROOT for CI/dev.
    """

    def __init__(self, github_org: str | None = None, github_token: str | None = None):
        self.github_org = github_org or os.getenv("GITHUB_ORG", "tali-assessments")
        self.github_token = github_token or os.getenv("GITHUB_TOKEN", "")
        self.mock_mode = os.getenv("GITHUB_MOCK_MODE", "true").lower() in {"1", "true", "yes"}
        self.mock_root = Path(os.getenv("GITHUB_MOCK_ROOT", "/tmp/tali_github_mock"))

    def _repo_name(self, task: Any) -> str:
        raw = getattr(task, "task_key", None) or (task.get("task_id") if isinstance(task, dict) else None) or getattr(task, "id", "task")
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

    def _run(self, args: list[str], cwd: Path) -> None:
        subprocess.run(args, cwd=cwd, check=False, capture_output=True)

    def _ensure_mock_repo(self, repo_name: str, files: Dict[str, str]) -> Path:
        repo = self.mock_root / self.github_org / repo_name
        repo.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self._run(["git", "init", "-b", "main"], repo)
        self._run(["git", "add", "."], repo)
        self._run(["git", "-c", "user.email=tali@local", "-c", "user.name=TALI", "commit", "-m", "Initialize task template"], repo)
        return repo

    def create_template_repo(self, task: Any) -> str:
        repo_name = self._repo_name(task)
        files = self._repo_files(task)
        if self.mock_mode:
            self._ensure_mock_repo(repo_name, files)
            return f"mock://{self.github_org}/{repo_name}"
        # Production: implement GitHub API (create repo, push files to main).
        # Requires GITHUB_TOKEN with repo scope. Return public clone URL.
        return f"https://github.com/{self.github_org}/{repo_name}.git"

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

        repo_url = f"https://github.com/{self.github_org}/{repo_name}.git"
        return BranchContext(repo_url=repo_url, branch_name=branch_name, clone_command=f"git clone --branch {branch_name} {repo_url}")

    def archive_assessment(self, assessment_id: int) -> Dict[str, Any]:
        return {"assessment_id": assessment_id, "archived": True, "mode": "mock" if self.mock_mode else "github"}
