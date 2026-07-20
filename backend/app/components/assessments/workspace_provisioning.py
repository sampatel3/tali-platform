"""Candidate-safe, local-only assessment workspace provisioning."""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
from typing import Any, Dict, List

from ...models.task import Task
from ...services.assessment_repository_service import sanitize_candidate_workspace_files
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root

# Keep the established logger category after extracting these helpers.
logger = logging.getLogger("app.components.assessments.service")


def _repo_files_from_structure(
    repo_structure: Dict[str, Any] | None,
) -> List[tuple[str, str]]:
    """Build a canonical, traversal-safe candidate repository manifest."""
    return list(sanitize_candidate_workspace_files(repo_structure).items())


def _execution_stdout_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("stdout") or "")

    logs = getattr(result, "logs", None)
    raw_stdout = getattr(logs, "stdout", None) if logs is not None else None
    if isinstance(raw_stdout, list):
        return "\n".join(str(item) for item in raw_stdout)
    if raw_stdout is not None:
        return str(raw_stdout)
    return str(getattr(result, "stdout", "") or "")


def _sandbox_operation_payload(result: Any) -> Dict[str, Any]:
    lines = _execution_stdout_text(result).strip().splitlines()
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sandbox_operation_succeeded(result: Any) -> tuple[bool, Dict[str, Any]]:
    payload = _sandbox_operation_payload(result)
    if payload.get("success") is not None:
        return bool(payload.get("success")), payload
    raw_returncode = payload.get("returncode", payload.get("exit_code"))
    try:
        return raw_returncode is not None and int(raw_returncode) == 0, payload
    except (TypeError, ValueError):
        return False, payload


def _workspace_repo_root(task: Task) -> str:
    repo_root = canonical_workspace_repo_root(task)
    root_path = PurePosixPath(repo_root)
    workspace_root = PurePosixPath("/workspace")
    reserved_name = root_path.name.casefold().rstrip(" .") in {
        "",
        ".",
        "..",
        ".git",
    }
    if root_path.parent != workspace_root or reserved_name:
        raise RuntimeError(f"Unsafe candidate workspace root: {repo_root!r}")
    return repo_root


def _ensure_workspace_repo_permissions(sandbox: Any, repo_root: str) -> bool:
    result = sandbox.run_code(
        "import json, pathlib, subprocess\n"
        f"repo_root=pathlib.Path({repo_root!r})\n"
        "payload={'success': False, 'stderr': ''}\n"
        "if not repo_root.exists():\n"
        "  payload['stderr'] = 'repo_root_missing'\n"
        "else:\n"
        "  proc = subprocess.run(['chmod', '-R', 'a+rwX', str(repo_root)], capture_output=True, text=True)\n"
        "  payload = {'success': proc.returncode == 0, 'stderr': (proc.stderr or '')[-500:]}\n"
        "print(json.dumps(payload))\n"
    )
    try:
        lines = _execution_stdout_text(result).strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
        ok = False
        if isinstance(payload, dict):
            if payload.get("success") is not None:
                ok = bool(payload.get("success"))
            else:
                raw_returncode = payload.get("returncode", payload.get("exit_code"))
                try:
                    ok = int(raw_returncode) == 0 if raw_returncode is not None else False
                except (TypeError, ValueError):
                    ok = False
        if not ok:
            logger.error(
                "Failed to normalize workspace permissions repo_root=%s stderr=%s",
                repo_root,
                str(payload.get("stderr") or ""),
            )
        return ok
    except Exception:
        logger.exception(
            "Failed to parse workspace permission output repo_root=%s", repo_root
        )
        return False


def _materialize_task_repository(
    sandbox: Any,
    task: Task,
    *,
    repo_files_from_structure_fn: Any = _repo_files_from_structure,
    workspace_repo_root_fn: Any = _workspace_repo_root,
    sandbox_operation_succeeded_fn: Any = _sandbox_operation_succeeded,
    ensure_workspace_repo_permissions_fn: Any = _ensure_workspace_repo_permissions,
) -> None:
    """Create a fresh, local-only Git worktree from the sanitized task manifest."""
    repo_files = repo_files_from_structure_fn(task.repo_structure)
    if not repo_files:
        raise RuntimeError("Task has no candidate repository files")

    files_api = getattr(sandbox, "files", None)
    if not files_api or not hasattr(files_api, "write"):
        raise RuntimeError("Sandbox files API is unavailable")

    repo_root = workspace_repo_root_fn(task)

    reset_result = sandbox.run_code(
        "import json, pathlib, shutil\n"
        f"repo = pathlib.Path({repo_root!r})\n"
        "payload = {'success': False, 'stderr': ''}\n"
        "try:\n"
        "  repo.parent.mkdir(parents=True, exist_ok=True)\n"
        "  if repo.is_symlink() or repo.is_file():\n"
        "    repo.unlink()\n"
        "  elif repo.exists():\n"
        "    shutil.rmtree(repo)\n"
        "  repo.mkdir(parents=True, exist_ok=False)\n"
        "  payload['success'] = True\n"
        "except Exception as exc:\n"
        "  payload['stderr'] = str(exc)[-500:]\n"
        "print(json.dumps(payload))\n"
    )
    reset_ok, reset_payload = sandbox_operation_succeeded_fn(reset_result)
    if not reset_ok:
        raise RuntimeError(
            "Failed to reset candidate repository: "
            f"{str(reset_payload.get('stderr') or 'unknown error')[-500:]}"
        )

    for rel_path, content in repo_files:
        target_path = f"{repo_root}/{rel_path}"
        files_api.write(target_path, content)

    git_result = sandbox.run_code(
        "import json, os, pathlib, shutil, subprocess\n"
        f"repo = pathlib.Path({repo_root!r})\n"
        "template = repo.parent / ('.' + repo.name + '-empty-git-template')\n"
        "payload = {'success': False, 'stderr': '', 'steps': []}\n"
        "try:\n"
        "  if template.exists():\n"
        "    shutil.rmtree(template)\n"
        "  template.mkdir(parents=True)\n"
        "  trusted_path = '/usr/local/bin:/usr/bin:/bin'\n"
        "  git_binary = shutil.which('git', path=trusted_path)\n"
        "  if not git_binary:\n"
        "    raise RuntimeError('trusted_git_binary_not_found')\n"
        "  git_env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}\n"
        "  git_env.update({'PATH': trusted_path, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'})\n"
        "  commands = [\n"
        "    [git_binary, 'init', '--template=' + str(template), '-b', 'candidate'],\n"
        "    [git_binary, 'config', '--local', 'credential.helper', ''],\n"
        "    [git_binary, 'config', '--local', 'core.hooksPath', '/dev/null'],\n"
        "    [git_binary, 'add', '-f', '--all'],\n"
        "    [git_binary, '-c', 'user.email=taali@local', '-c', 'user.name=TAALI', 'commit', '-m', 'Initial assessment context'],\n"
        "  ]\n"
        "  commands_ok = True\n"
        "  for args in commands:\n"
        "    proc = subprocess.run(args, cwd=repo, env=git_env, check=False, capture_output=True, text=True)\n"
        "    payload['steps'].append({'command': args[:2], 'returncode': proc.returncode})\n"
        "    if proc.returncode != 0:\n"
        "      payload['stderr'] = (proc.stderr or proc.stdout or '')[-500:]\n"
        "      commands_ok = False\n"
        "      break\n"
        "  if commands_ok:\n"
        "    remote = subprocess.run([git_binary, 'remote'], cwd=repo, env=git_env, check=False, capture_output=True, text=True)\n"
        "    remote_refs = subprocess.run([git_binary, 'for-each-ref', '--format=%(refname)', 'refs/remotes'], cwd=repo, env=git_env, check=False, capture_output=True, text=True)\n"
        "    local_refs = subprocess.run([git_binary, 'for-each-ref', '--format=%(refname)', 'refs/heads'], cwd=repo, env=git_env, check=False, capture_output=True, text=True)\n"
        "    remote_urls = subprocess.run([git_binary, 'config', '--local', '--get-regexp', r'^remote\\..*\\.url$'], cwd=repo, env=git_env, check=False, capture_output=True, text=True)\n"
        "    config_text = (repo / '.git' / 'config').read_text(encoding='utf-8', errors='replace')\n"
        "    local_only = (\n"
        "      remote.returncode == 0 and not (remote.stdout or '').strip()\n"
        "      and remote_refs.returncode == 0 and not (remote_refs.stdout or '').strip()\n"
        "      and local_refs.returncode == 0 and (local_refs.stdout or '').strip() == 'refs/heads/candidate'\n"
        "      and remote_urls.returncode in (0, 1) and not (remote_urls.stdout or '').strip()\n"
        "      and '[remote ' not in config_text.casefold()\n"
        "      and 'x-access-token' not in config_text.casefold()\n"
        "    )\n"
        "    payload['success'] = bool(local_only)\n"
        "    if not local_only:\n"
        "      payload['stderr'] = 'local_git_perimeter_verification_failed'\n"
        "except Exception as exc:\n"
        "  payload['stderr'] = str(exc)[-500:]\n"
        "finally:\n"
        "  if template.exists():\n"
        "    shutil.rmtree(template, ignore_errors=True)\n"
        "print(json.dumps(payload))\n"
    )
    git_ok, git_payload = sandbox_operation_succeeded_fn(git_result)
    if not git_ok:
        raise RuntimeError(
            "Failed to initialize local-only candidate Git repository: "
            f"{str(git_payload.get('stderr') or 'unknown error')[-500:]}"
        )

    if not ensure_workspace_repo_permissions_fn(sandbox, repo_root):
        raise RuntimeError(
            f"Failed to normalize workspace permissions for {repo_root}"
        )

    logger.info("Materialized %d repository files under %s", len(repo_files), repo_root)
