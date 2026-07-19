from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-.")
    return cleaned or "task"


def task_template_repository_name(task: Any) -> str:
    """Return the stable GitHub/mock repository name for a task.

    Database tasks carry a persisted, globally unique identity so two
    organizations or case variants can never share a mutable template repo.
    Lightweight legacy/spec objects without that field retain the historical
    task-key naming contract. Lossy legacy names use a bounded SHA-256 suffix.
    """

    persisted = (
        task.get("template_repository_name")
        if isinstance(task, dict)
        else getattr(task, "template_repository_name", None)
    )
    if persisted is not None:
        if not is_safe_repository_name(persisted):
            raise ValueError("task template repository identity is malformed")
        return persisted

    raw = (
        getattr(task, "task_key", None)
        or (task.get("task_id") if isinstance(task, dict) else None)
        or (task.get("id") if isinstance(task, dict) else getattr(task, "id", "task"))
    )
    raw_text = str(raw)
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_text).strip("-").lower()
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    if (
        name not in {"", ".", ".."}
        and name.casefold() != ".git"
        and len(name) <= 100
        and raw_text.lower() == name
    ):
        return name
    readable = (
        "task"
        if name in {"", ".", ".."} or name.casefold() == ".git"
        else name.strip("-.")[:83].rstrip("-.") or "task"
    )
    return f"{readable}-{digest}"


def is_safe_repository_name(value: Any) -> bool:
    """Return whether ``value`` is one inert GitHub/filesystem path segment."""

    return bool(
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and value.casefold() != ".git"
        and len(value) <= 100
        and value == value.lower()
        and re.fullmatch(r"[A-Za-z0-9._-]+", value)
    )


def _repo_root() -> Path:
    root = os.getenv("TASK_REPOS_ROOT", "/tmp/taali_task_repos")
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_safe_repo_file_path(value: Any) -> bool:
    """Reject paths that can escape or mutate Git's control directory."""

    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or PureWindowsPath(normalized).drive:
        return False
    parts = PurePosixPath(normalized).parts
    return bool(parts) and ".." not in parts and all(
        part.casefold() != ".git" for part in parts
    )


def safe_repo_file_target(repo_dir: Path, value: Any) -> Path | None:
    """Resolve a repository file without following pre-existing symlinks.

    The lexical check prevents ``..``/absolute escapes.  Walking the existing
    components separately also prevents a checkout or stale mock repository
    from redirecting an otherwise innocent path through a symlink (including
    an alias back into ``.git``).
    """

    if not is_safe_repo_file_path(value):
        return None
    parts = PurePosixPath(str(value).replace("\\", "/")).parts
    root = repo_dir.resolve()
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            return None
    try:
        current.resolve(strict=False).relative_to(root)
    except ValueError:
        return None
    return current


def normalize_repo_file_content(content: Any) -> str:
    if not isinstance(content, str):
        return str(content)

    if "\n" in content or "\r" in content:
        return content

    if not any(token in content for token in ("\\n", "\\r", "\\t", "\\'", '\\"', "\\\\")):
        return content

    # Decode only the legacy escape set this compatibility shim advertises.
    # ``bytes(...).decode("unicode_escape")`` also reinterprets every non-ASCII
    # UTF-8 byte, corrupting otherwise valid source such as ``café\\n``.
    escapes = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "'": "'",
        '"': '"',
        "\\": "\\",
    }
    decoded: list[str] = []
    index = 0
    while index < len(content):
        current = content[index]
        if current == "\\" and index + 1 < len(content):
            escaped = escapes.get(content[index + 1])
            if escaped is not None:
                decoded.append(escaped)
                index += 2
                continue
        decoded.append(current)
        index += 1
    normalized = "".join(decoded)
    return normalized if normalized else content


def normalize_repo_files(repo_structure: Dict[str, Any] | None) -> Dict[str, str]:
    files = (repo_structure or {}).get("files") or {}
    if isinstance(files, list):
        normalized = {}
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if not isinstance(path, str) or not path.strip():
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


def _write_repo_files(repo_dir: Path, repo_structure: Dict[str, Any] | None) -> None:
    files = normalize_repo_files(repo_structure)
    if not files:
        return

    for rel_path, content in files.items():
        target = safe_repo_file_target(repo_dir, rel_path)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after creating parents so a stale/racing symlink cannot turn
        # the mkdir step into an out-of-repository write.
        target = safe_repo_file_target(repo_dir, rel_path)
        if target is None:
            continue
        target.write_text(content, encoding="utf-8")


def task_main_repo_path(task: Any) -> str:
    key = getattr(task, "task_key", None) or f"task-{getattr(task, 'id', 'unknown')}"
    name = getattr(task, "name", None) or "assessment-task"
    task_id = getattr(task, "id", None)
    org_id = getattr(task, "organization_id", None)
    # Two tasks (possibly in different orgs) can share the same key+name —
    # qualify the directory with the task id and org id so distinct tasks
    # never share a snapshot dir and recreate_task_main_repo can't rmtree
    # another task's repo out from under it.
    identity = "-".join(
        str(part) for part in (org_id, task_id) if part is not None
    ) or "x"
    repo_dir = _repo_root() / f"{_slug(key)}-{_slug(name)}-{_slug(identity)}"
    return str(repo_dir)


def recreate_task_main_repo(task: Any) -> str:
    """Recreate the canonical `main` repo snapshot for a task.

    Returns absolute path to the recreated repo directory.
    """
    repo_dir = Path(task_main_repo_path(task))

    if repo_dir.is_symlink():
        repo_dir.unlink()
    elif repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    _write_repo_files(repo_dir, getattr(task, "repo_structure", None))

    # Best-effort git init to make this a real canonical repo snapshot.
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=False, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=False, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=TAALI", "-c", "user.email=noreply@taali.ai", "commit", "-m", "Initialize task repo"],
        cwd=repo_dir,
        check=False,
        capture_output=True,
    )

    return str(repo_dir)
