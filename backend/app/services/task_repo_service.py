from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-.")
    return cleaned or "task"


def _repo_root() -> Path:
    root = os.getenv("TASK_REPOS_ROOT", "/tmp/tali_task_repos")
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_repo_files(repo_dir: Path, repo_structure: Dict[str, Any] | None) -> None:
    files = (repo_structure or {}).get("files") or {}
    if isinstance(files, list):
        normalized = {}
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if not path:
                continue
            normalized[path] = entry.get("content", "")
        files = normalized

    if not isinstance(files, dict):
        return

    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not rel_path.strip():
            continue
        safe_rel = rel_path.replace("\\", "/").lstrip("/")
        if ".." in Path(safe_rel).parts:
            continue
        target = repo_dir / safe_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else str(content))


def recreate_task_main_repo(task: Any) -> str:
    """Recreate the canonical `main` repo snapshot for a task.

    Returns absolute path to the recreated repo directory.
    """
    key = getattr(task, "task_key", None) or f"task-{getattr(task, 'id', 'unknown')}"
    name = getattr(task, "name", None) or "assessment-task"
    repo_dir = _repo_root() / f"{_slug(key)}-{_slug(name)}"

    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    _write_repo_files(repo_dir, getattr(task, "repo_structure", None))

    # Best-effort git init to make this a real canonical repo snapshot.
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=False, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=False, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=TALI", "-c", "user.email=noreply@tali.dev", "commit", "-m", "Initialize task repo"],
        cwd=repo_dir,
        check=False,
        capture_output=True,
    )

    return str(repo_dir)
