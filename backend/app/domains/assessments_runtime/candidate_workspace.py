from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
from pathlib import PurePosixPath

from fastapi import HTTPException

from ...components.assessments.workspace_provisioning import _workspace_repo_root
from ...models.task import Task
from ...services.task_repo_service import normalize_repo_files

logger = logging.getLogger(__name__)

MAX_CANDIDATE_SNAPSHOT_FILES = 100
MAX_CANDIDATE_NEW_FILES = 20
MAX_CANDIDATE_SNAPSHOT_BYTES = 2_000_000
MAX_CANDIDATE_FILE_BYTES = 500_000
MAX_EXECUTION_OUTPUT_CHARS = 16_000
MAX_REVISION_SCAN_FILES = 250
_PYTHON_MODULE_PART_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"

# Candidate-controlled file APIs must never reach repository/runtime control
# state. ``.gitignore`` remains editable; the exact control directory/file
# names below do not. The same policy is mirrored by AssessmentToolExecutor.
_PROTECTED_REPO_PATH_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
    }
)
_PROTECTED_REPO_FILENAMES = frozenset({".env", ".gitmodules"})


def execution_stdout_text(result: object) -> str:
    if isinstance(result, dict):
        return str(result.get("stdout") or result.get("out") or "")

    logs = getattr(result, "logs", None)
    raw_stdout = getattr(logs, "stdout", None) if logs is not None else None
    if isinstance(raw_stdout, list):
        return "\n".join(str(item) for item in raw_stdout)
    if raw_stdout is not None:
        return str(raw_stdout)
    return str(getattr(result, "stdout", "") or "")


def truncate_output(value: object, cap: int = MAX_EXECUTION_OUTPUT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= cap:
        return text
    return f"{text[:cap]}\n... [truncated {len(text) - cap} chars]"


def extract_process_output(result: object) -> tuple[str, str, int | None]:
    if isinstance(result, dict):
        stdout = str(result.get("stdout") or result.get("out") or "")
        stderr = str(result.get("stderr") or result.get("err") or "")
        exit_code = result.get("exit_code")
        try:
            return stdout, stderr, int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            return stdout, stderr, None

    stdout = str(getattr(result, "stdout", "") or getattr(result, "out", "") or "")
    stderr = str(getattr(result, "stderr", "") or getattr(result, "err", "") or "")
    exit_code = getattr(result, "exit_code", None)
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return stdout, stderr, exit_code


def sanitize_repo_path(path: str | None) -> str:
    raw_path = str(path or "").strip().replace("\\", "/")
    if not raw_path:
        return ""
    try:
        normalized = PurePosixPath(raw_path)
    except Exception:
        return ""
    if normalized.is_absolute():
        return ""
    parts = [str(part).strip() for part in normalized.parts if str(part).strip()]
    if not parts or any(part in {".", ".."} for part in parts):
        return ""
    folded_parts = [part.casefold() for part in parts]
    if any(part in _PROTECTED_REPO_PATH_PARTS for part in folded_parts):
        return ""
    if folded_parts[-1] in _PROTECTED_REPO_FILENAMES:
        return ""
    if any("\x00" in part for part in parts):
        return ""
    return "/".join(parts)


def _task_extra_data(task: Task) -> dict:
    extra = getattr(task, "extra_data", None)
    return extra if isinstance(extra, dict) else {}


def normalize_runtime_repo_files(
    entries: list[object] | None,
    *,
    task: Task | None = None,
) -> dict[str, str]:
    raw_entries = list(entries or [])
    if len(raw_entries) > MAX_CANDIDATE_SNAPSHOT_FILES:
        raise HTTPException(status_code=413, detail="Repository snapshot contains too many files")

    baseline_paths = set(normalize_repo_files(getattr(task, "repo_structure", None))) if task else set()
    normalized: dict[str, str] = {}
    total_bytes = 0
    for entry in raw_entries:
        path = sanitize_repo_path(getattr(entry, "path", None))
        if not path:
            raise HTTPException(status_code=400, detail="Invalid or protected repository file path")
        if path in normalized:
            raise HTTPException(status_code=400, detail=f"Duplicate repository file path: {path}")
        content = str(getattr(entry, "content", "") or "")
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_CANDIDATE_SNAPSHOT_BYTES:
            raise HTTPException(status_code=413, detail="Repository snapshot is too large")
        normalized[path] = content

    if task is not None:
        new_file_count = len(set(normalized) - baseline_paths)
        if new_file_count > MAX_CANDIDATE_NEW_FILES:
            raise HTTPException(
                status_code=413,
                detail=f"Repository snapshot may add at most {MAX_CANDIDATE_NEW_FILES} files",
            )
    return normalized


def inspect_sandbox_repo_target(sandbox: object, repo_root: str, rel_path: str) -> dict:
    """Inspect a candidate path with lstat, never following links."""
    safe_path = sanitize_repo_path(rel_path)
    if not safe_path:
        return {"safe": False, "reason": "invalid_or_protected_path"}

    result = sandbox.run_code(
        "import json, pathlib, stat\n"
        f"root = pathlib.Path({repo_root!r})\n"
        f"parts = {safe_path.split('/')!r}\n"
        "answer = {'safe': True, 'exists': False, 'kind': 'missing', 'reason': None}\n"
        "try:\n"
        "  root_stat = root.lstat()\n"
        "  if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):\n"
        "    answer.update(safe=False, reason='unsafe_repo_root')\n"
        "  else:\n"
        "    current = root\n"
        "    for index, part in enumerate(parts):\n"
        "      current = current / part\n"
        "      is_last = index == len(parts) - 1\n"
        "      try:\n"
        "        item_stat = current.lstat()\n"
        "      except FileNotFoundError:\n"
        "        break\n"
        "      mode = item_stat.st_mode\n"
        "      if stat.S_ISLNK(mode):\n"
        "        answer.update(safe=False, reason='symlink')\n"
        "        break\n"
        "      if not is_last and not stat.S_ISDIR(mode):\n"
        "        answer.update(safe=False, reason='non_directory_parent')\n"
        "        break\n"
        "      if is_last:\n"
        "        answer['exists'] = True\n"
        "        if stat.S_ISREG(mode):\n"
        "          answer['kind'] = 'file'\n"
        "          answer['size'] = item_stat.st_size\n"
        "          if item_stat.st_nlink != 1:\n"
        "            answer.update(safe=False, reason='hard_link')\n"
        "        elif stat.S_ISDIR(mode):\n"
        "          answer['kind'] = 'directory'\n"
        "        else:\n"
        "          answer.update(safe=False, kind='special', reason='special_file')\n"
        "except Exception as exc:\n"
        "  answer = {'safe': False, 'exists': False, 'kind': 'unknown', 'reason': exc.__class__.__name__}\n"
        "print(json.dumps(answer))\n"
    )
    try:
        lines = execution_stdout_text(result).strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
    except Exception:
        logger.warning("Could not validate candidate repo path=%s", safe_path)
        return {"safe": False, "reason": "path_validation_failed"}
    if not isinstance(payload, dict) or not isinstance(payload.get("safe"), bool):
        return {"safe": False, "reason": "path_validation_failed"}
    return payload


def candidate_file_revision(content: str | bytes) -> str:
    """Return the opaque revision used by candidate file APIs."""
    payload = content if isinstance(content, bytes) else str(content).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_candidate_repo_file(
    sandbox: object,
    repo_root: str,
    rel_path: str,
    *,
    allow_missing: bool = False,
) -> dict | None:
    """Read one safe UTF-8 file and attach its content-addressed revision."""
    safe_path = sanitize_repo_path(rel_path)
    if not safe_path:
        raise HTTPException(status_code=400, detail="Invalid or protected repository file path")

    state = inspect_sandbox_repo_target(sandbox, repo_root, safe_path)
    if not state.get("safe"):
        raise HTTPException(status_code=400, detail="Repository path is not a safe regular file")
    if not state.get("exists"):
        if allow_missing:
            return None
        raise HTTPException(status_code=404, detail="Repository file not found")
    if state.get("kind") != "file":
        raise HTTPException(status_code=400, detail="Repository path is not a regular file")
    try:
        target_size = int(state.get("size"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Repository file size could not be validated")
    if target_size > MAX_CANDIDATE_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Repository file is too large to open")

    files_api = getattr(sandbox, "files", None)
    if files_api is None or not hasattr(files_api, "read"):
        raise HTTPException(status_code=503, detail="Workspace file access is unavailable")
    try:
        raw_content = files_api.read(f"{repo_root}/{safe_path}")
    except Exception as exc:  # noqa: BLE001 - provider-specific file errors
        raise HTTPException(status_code=404, detail="Repository file not found") from exc

    if isinstance(raw_content, bytes):
        byte_length = len(raw_content)
        if byte_length > MAX_CANDIDATE_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Repository file is too large to open")
        try:
            content = raw_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="Only UTF-8 text files can be opened") from exc
    elif isinstance(raw_content, str):
        content = raw_content
        byte_length = len(content.encode("utf-8"))
        if byte_length > MAX_CANDIDATE_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Repository file is too large to open")
    else:
        raise HTTPException(status_code=415, detail="Only UTF-8 text files can be opened")
    if "\x00" in content:
        raise HTTPException(status_code=415, detail="Only UTF-8 text files can be opened")

    return {
        "path": safe_path,
        "content": content,
        "byte_length": byte_length,
        "revision": candidate_file_revision(content),
    }


def workspace_file_revisions(
    sandbox: object,
    repo_root: str,
    *,
    max_files: int = MAX_REVISION_SCAN_FILES,
) -> dict[str, str] | None:
    """Snapshot safe workspace file revisions without returning file contents.

    ``None`` means the bounded scan could not prove a complete snapshot. Callers
    can then fall back to explicit tool paths instead of reporting a partial map.
    """
    protected_parts = sorted(_PROTECTED_REPO_PATH_PARTS)
    protected_files = sorted(_PROTECTED_REPO_FILENAMES)
    try:
        result = sandbox.run_code(
            "import hashlib, json, os, pathlib, stat\n"
            f"root = pathlib.Path({repo_root!r})\n"
            f"protected_parts = set({protected_parts!r})\n"
            f"protected_files = set({protected_files!r})\n"
            f"max_files = {max(1, int(max_files))}\n"
            "revisions = {}\n"
            "truncated = False\n"
            "for current_root, dir_names, file_names in os.walk(root, followlinks=False):\n"
            "  current = pathlib.Path(current_root)\n"
            "  kept_dirs = []\n"
            "  for name in sorted(dir_names):\n"
            "    candidate = current / name\n"
            "    if name.casefold() in protected_parts or candidate.is_symlink():\n"
            "      continue\n"
            "    kept_dirs.append(name)\n"
            "  dir_names[:] = kept_dirs\n"
            "  for name in sorted(file_names):\n"
            "    target = current / name\n"
            "    try:\n"
            "      relative = target.relative_to(root).as_posix()\n"
            "      parts = [part.casefold() for part in pathlib.PurePosixPath(relative).parts]\n"
            "      item_stat = target.lstat()\n"
            "      if (not stat.S_ISREG(item_stat.st_mode) or item_stat.st_nlink != 1\n"
            "          or any(part in protected_parts for part in parts)\n"
            "          or parts[-1] in protected_files):\n"
            "        continue\n"
            f"      if item_stat.st_size > {MAX_CANDIDATE_FILE_BYTES}:\n"
            "        continue\n"
            "      if len(revisions) >= max_files:\n"
            "        truncated = True\n"
            "        break\n"
            "      revisions[relative] = hashlib.sha256(target.read_bytes()).hexdigest()\n"
            "    except Exception:\n"
            "      truncated = True\n"
            "      break\n"
            "  if truncated:\n"
            "    break\n"
            "print(json.dumps({'revisions': revisions, 'truncated': truncated}, sort_keys=True))\n"
        )
        lines = execution_stdout_text(result).strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
    except Exception:
        logger.info("Could not snapshot candidate workspace revisions", exc_info=True)
        return None
    if not isinstance(payload, dict) or payload.get("truncated") is not False:
        return None
    raw_revisions = payload.get("revisions")
    if not isinstance(raw_revisions, dict):
        return None
    revisions: dict[str, str] = {}
    for raw_path, raw_revision in raw_revisions.items():
        safe_path = sanitize_repo_path(str(raw_path))
        revision = str(raw_revision or "").lower()
        if not safe_path or not re.fullmatch(r"[0-9a-f]{64}", revision):
            return None
        revisions[safe_path] = revision
    return revisions


def _require_writable_regular_target(sandbox: object, repo_root: str, rel_path: str) -> None:
    state = inspect_sandbox_repo_target(sandbox, repo_root, rel_path)
    if not state.get("safe"):
        raise HTTPException(status_code=400, detail="Repository path is not a safe regular file")
    if state.get("exists") and state.get("kind") != "file":
        raise HTTPException(status_code=400, detail="Repository path is not a regular file")


def bounded_execution_result(result: object, *, repo_root: str) -> dict:
    if not isinstance(result, dict):
        stdout, stderr, exit_code = extract_process_output(result)
        result = {
            "success": exit_code in (None, 0),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
    bounded = dict(result)
    bounded["stdout"] = truncate_output(bounded.get("stdout"))
    bounded["stderr"] = truncate_output(bounded.get("stderr"))
    if bounded.get("error") is not None:
        bounded["error"] = truncate_output(bounded.get("error"), 2_000)
    bounded["working_dir"] = repo_root
    for key in ("sandbox_id", "session_id", "repo_url", "clone_command", "environment"):
        bounded.pop(key, None)
    return bounded


def sync_repo_files_to_sandbox(sandbox: object, repo_root: str, repo_files: dict[str, str]) -> None:
    if not repo_files:
        return
    files_api = getattr(sandbox, "files", None)
    if files_api is None or not hasattr(files_api, "write"):
        raise HTTPException(status_code=500, detail="Sandbox file sync is unavailable")

    for rel_path, content in repo_files.items():
        safe_path = sanitize_repo_path(rel_path)
        if not safe_path:
            raise HTTPException(status_code=400, detail="Invalid or protected repository file path")
        _require_writable_regular_target(sandbox, repo_root, safe_path)
        target_path = f"{repo_root}/{safe_path}"
        sandbox.run_code(
            "import pathlib\n"
            f"pathlib.Path({target_path!r}).parent.mkdir(parents=True, exist_ok=True)\n"
        )
        files_api.write(target_path, str(content or ""))


def _python_module_path(selected_file_path: str | None) -> str | None:
    path = sanitize_repo_path(selected_file_path)
    if not path or not path.lower().endswith(".py"):
        return None

    parts = [part for part in path[:-3].split("/") if part]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or any(not re.match(_PYTHON_MODULE_PART_RE, part) for part in parts):
        return None
    return ".".join(parts)


def _shell_python_prefix() -> str:
    return (
        'export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"; '
        'PYTHON_BIN="./.venv/bin/python"; '
        '[ -x "$PYTHON_BIN" ] || PYTHON_BIN=python; '
    )


def _build_run_command(selected_file_path: str | None, *, task: Task | None = None) -> str | None:
    path = sanitize_repo_path(selected_file_path)
    if not path:
        return None

    basename = path.rsplit("/", 1)[-1]
    quoted_path = shlex.quote(path)
    lower_path = path.lower()
    shell_prefix = _shell_python_prefix()
    if lower_path.endswith(".py"):
        if path.startswith("tests/") or "/tests/" in path or basename.startswith("test_"):
            test_runner = (
                str((_task_extra_data(task).get("test_runner") or {}).get("command") or "").strip()
                if task
                else ""
            )
            if test_runner:
                return f"{shell_prefix}{test_runner} {quoted_path}"
            return f'{shell_prefix}"$PYTHON_BIN" -m pytest -q {quoted_path}'

        module_path = _python_module_path(path)
        if module_path:
            return f'{shell_prefix}"$PYTHON_BIN" -m {shlex.quote(module_path)}'
        return f'{shell_prefix}"$PYTHON_BIN" {quoted_path}'
    if lower_path.endswith((".sh", ".bash")):
        return f"bash {quoted_path}"
    if lower_path.endswith((".js", ".mjs", ".cjs")):
        return f"node {quoted_path}"
    return None


def run_selected_repo_file(
    e2b: object,
    sandbox: object,
    task: Task,
    selected_file_path: str | None,
) -> dict:
    repo_root = _workspace_repo_root(task)
    command = _build_run_command(selected_file_path, task=task)
    if not command:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": "No default Run action exists for this file type. Ask Claude to run a repository command or select a runnable source/test file.",
            "results": [],
            "command": None,
            "working_dir": repo_root,
        }

    try:
        process = e2b.run_command(sandbox, command, cwd=repo_root, timeout=60)
        stdout, stderr, exit_code = extract_process_output(process)
        success = exit_code in (None, 0)
        return {
            "success": success,
            "stdout": truncate_output(stdout),
            "stderr": truncate_output(stderr),
            "error": None if success else (
                f"Command exited with code {exit_code}" if exit_code is not None else "Command failed"
            ),
            "results": [],
            "command": command,
            "working_dir": repo_root,
            "exit_code": exit_code,
        }
    except Exception as exc:
        stdout, stderr, exit_code = extract_process_output(exc)
        return {
            "success": False,
            "stdout": truncate_output(stdout),
            "stderr": truncate_output(stderr),
            "error": truncate_output(exc, 2_000),
            "results": [],
            "command": command,
            "working_dir": repo_root,
            "exit_code": exit_code,
        }
