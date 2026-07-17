"""Assessment business logic: start, submit, CV upload, scoring orchestration."""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from typing import Any, Dict, List

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...platform.config import settings
from ...models.assessment import Assessment, AssessmentStatus
from ...models.organization import Organization
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.task import Task
from ...components.integrations.e2b.service import E2BService
from ...services.document_service import process_document_upload
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root
from ...services.task_repo_service import normalize_repo_files
from ...domains.assessments_runtime.role_support import refresh_application_score_cache
from ...domains.assessments_runtime.workspace_serialization import assessment_workspace_mutex, prepare_assessment_workspace_mutex
from .claude_budget import (  # noqa: F401 - start-runtime monkeypatch seams
    build_claude_budget_snapshot,
    resolve_effective_budget_limit_usd,
)
from .interrogation import render_opener  # noqa: F401 - start-runtime seam
from .submission_runtime import (
    _durable_candidate_branch_snapshot,
    submit_assessment_impl,
)
from .terminal_runtime import (  # noqa: F401 - start-runtime monkeypatch seams
    resolve_ai_mode,
    terminal_capabilities,
)
from .assessment_guards import enforce_not_paused  # noqa: F401 - compatibility re-export

logger = logging.getLogger(__name__)

INSUFFICIENT_CREDITS_DETAIL = "Insufficient credits. Purchase credits to start this assessment."
CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE = (
    "This assessment is not available yet. Please contact the hiring team to continue."
)
ORG_INSUFFICIENT_CREDITS_MESSAGE = (
    "No assessment credits available. Purchase credits before creating a new assessment."
)
ROLE_BUDGET_EXHAUSTED_MESSAGE = (
    "This role's monthly AI budget cannot fund another assessment call. "
    "Increase the role budget before continuing."
)
ORG_RESERVED_CREDITS_MESSAGE = (
    "All available credits are already reserved for pending assessments. Purchase more credits before creating another assessment."
)

from .repository import (
    utcnow,
    ensure_utc,
    resume_code_for_assessment,
    append_assessment_timeline_event,
    time_remaining_seconds,
)


def _repo_files_from_structure(repo_structure: Dict[str, Any] | None) -> List[tuple[str, str]]:
    """Normalize repo_structure payload into (path, content) tuples."""
    return list(normalize_repo_files(repo_structure).items())


def _task_extra_data(task: Task) -> Dict[str, Any]:
    extra = getattr(task, "extra_data", None)
    return extra if isinstance(extra, dict) else {}


def _extract_process_output(result: Any) -> tuple[str, str, int | None]:
    if isinstance(result, dict):
        stdout = str(result.get("stdout") or result.get("out") or "")
        stderr = str(result.get("stderr") or result.get("err") or "")
        exit_code = result.get("exit_code")
        try:
            exit_code = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            exit_code = None
        return stdout, stderr, exit_code

    stdout = str(getattr(result, "stdout", "") or getattr(result, "out", "") or "")
    stderr = str(getattr(result, "stderr", "") or getattr(result, "err", "") or "")
    exit_code = getattr(result, "exit_code", None)
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return stdout, stderr, exit_code


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


def _trim_bootstrap_output(text: str, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _workspace_repo_root(task: Task) -> str:
    return canonical_workspace_repo_root(task)


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
        logger.exception("Failed to parse workspace permission output repo_root=%s", repo_root)
        return False


def _clone_assessment_branch_into_workspace(sandbox: Any, assessment: Assessment, task: Task) -> bool:
    repo_url = getattr(assessment, "assessment_repo_url", None)
    branch_name = getattr(assessment, "assessment_branch", None)
    if not repo_url or not branch_name:
        return False
    repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
    raw_repo_url = str(repo_url)
    if raw_repo_url.startswith("mock://"):
        # In explicit mock mode, clone from the local mock mirror instead of using a network URL.
        mock_rel = raw_repo_url.replace("mock://", "", 1).strip("/")
        clone_url = str((repo_service.mock_root / mock_rel).resolve())
    else:
        clone_url = repo_service.authenticated_repo_url(raw_repo_url)
    repo_root = _workspace_repo_root(task)
    result = sandbox.run_code(
        "import json, pathlib, subprocess\n"
        f"repo_root=pathlib.Path({repo_root!r})\n"
        "repo_root.parent.mkdir(parents=True, exist_ok=True)\n"
        "subprocess.run(['rm','-rf',str(repo_root)], check=False, capture_output=True)\n"
        f"args=['git','clone','--branch',{branch_name!r},{clone_url!r},str(repo_root)]\n"
        "p=subprocess.run(args, capture_output=True, text=True)\n"
        "payload={'returncode': p.returncode, 'stderr': p.stderr[-500:]}\n"
        "print(json.dumps(payload))\n"
    )
    try:
        stdout_text = _execution_stdout_text(result)
        lines = stdout_text.strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
        ok = int(payload.get("returncode", 1)) == 0
        if not ok:
            logger.error(
                "Failed to clone assessment branch into sandbox repo_root=%s branch=%s stderr=%s",
                repo_root,
                branch_name,
                str(payload.get("stderr") or ""),
            )
            return False
        return _ensure_workspace_repo_permissions(sandbox, repo_root)
    except Exception:
        logger.exception("Failed to parse clone command output for assessment=%s", getattr(assessment, "id", None))
        return False


def _recover_retry_sandbox_from_pushed_branch(
    e2b: Any,
    assessment: Assessment,
    task: Task,
) -> Any:
    """Rebuild a killed scoring sandbox from the exact submitted Git head.

    The submission runtime has already checked the durable push marker.  We
    validate it again here, clone the candidate branch into a fresh sandbox,
    and compare the cloned HEAD with the recorded submission SHA before any
    tests or rubric providers can see the workspace.
    """
    snapshot = _durable_candidate_branch_snapshot(assessment)
    if snapshot is None:
        raise RuntimeError("candidate branch push checkpoint is unavailable")

    sandbox = e2b.create_sandbox()
    try:
        if not _clone_assessment_branch_into_workspace(sandbox, assessment, task):
            raise RuntimeError("failed to clone pushed candidate branch")

        cloned_evidence = _collect_git_evidence_from_sandbox(
            sandbox,
            _workspace_repo_root(task),
        )
        cloned_head = str(cloned_evidence.get("head_sha") or "").strip()
        if cloned_head != snapshot["head_sha"]:
            raise RuntimeError(
                "recovered candidate branch head does not match submission checkpoint"
            )

        bootstrap = _run_workspace_bootstrap(
            e2b,
            sandbox,
            task,
            _workspace_repo_root(task),
        )
        if not bootstrap.get("success") and bootstrap.get("must_succeed"):
            raise RuntimeError("failed to bootstrap recovered candidate workspace")

        append_assessment_timeline_event(
            assessment,
            "assessment_scoring_sandbox_recovered",
            {
                "source": "pushed_candidate_branch",
                "branch": snapshot["branch"],
                "head_sha": snapshot["head_sha"],
                "bootstrap_ran": bool(bootstrap.get("ran")),
                "bootstrap_success": bool(bootstrap.get("success")),
            },
        )
        return sandbox
    except Exception:
        try:
            e2b.close_sandbox(sandbox)
        except Exception:
            logger.exception(
                "Failed to close rejected recovery sandbox assessment_id=%s",
                getattr(assessment, "id", None),
            )
        raise


def _read_sandbox_repo_files(sandbox: Any, repo_root: str, max_files: int = 200, max_bytes: int = 500_000) -> Dict[str, Any] | None:
    """Read the live repo file tree from the sandbox.

    Returns a `repo_structure` dict matching the shape of `task.repo_structure`
    (so the candidate UI can rehydrate the editor with the latest content),
    or `None` on failure.
    """
    try:
        # Exclude generated / vendored content. Without this, ``.venv/``
        # bytecode + pip cache leaks into the candidate's file explorer
        # and drowns the actual repo content (assessment 77,
        # 2026-05-26 — 100+ ``.cpython-312.pyc`` rows in the tree).
        result = sandbox.run_code(
            "import json, os, pathlib\n"
            f"repo_root = pathlib.Path({repo_root!r})\n"
            f"MAX_FILES = {int(max_files)}\n"
            f"MAX_BYTES = {int(max_bytes)}\n"
            "EXCLUDE_DIR_NAMES = {\n"
            "  '.git', '.venv', 'venv', '.tox', '.mypy_cache',\n"
            "  '.pytest_cache', '.ruff_cache', '__pycache__',\n"
            "  'node_modules', '.next', 'dist', 'build', '.idea',\n"
            "  '.vscode',\n"
            "}\n"
            "EXCLUDE_SUFFIXES = ('.pyc', '.pyo', '.pyd')\n"
            "EXCLUDE_NAMES = {'.DS_Store'}\n"
            "files = {}\n"
            "skipped = []\n"
            "if repo_root.exists():\n"
            "  for path in sorted(repo_root.rglob('*')):\n"
            "    if not path.is_file():\n"
            "      continue\n"
            "    rel = path.relative_to(repo_root).as_posix()\n"
            "    parts = rel.split('/')\n"
            "    if any(p in EXCLUDE_DIR_NAMES for p in parts[:-1]):\n"
            "      continue\n"
            "    if path.name in EXCLUDE_NAMES or rel.endswith(EXCLUDE_SUFFIXES):\n"
            "      continue\n"
            "    if len(files) >= MAX_FILES:\n"
            "      skipped.append(rel)\n"
            "      continue\n"
            "    try:\n"
            "      raw = path.read_bytes()\n"
            "      if len(raw) > MAX_BYTES:\n"
            "        skipped.append(rel)\n"
            "        continue\n"
            "      files[rel] = raw.decode('utf-8', 'replace')\n"
            "    except Exception:\n"
            "      skipped.append(rel)\n"
            "print(json.dumps({'files': files, 'skipped': skipped}))\n"
        )
        out = _execution_stdout_text(result).strip().splitlines()
        if not out:
            return None
        payload = json.loads(out[-1])
        files = payload.get("files") or {}
        if not isinstance(files, dict) or not files:
            return None
        return {"files": files}
    except Exception:
        logger.exception("Failed to read sandbox repo files repo_root=%s", repo_root)
        return None


def _materialize_task_repository(sandbox: Any, task: Task) -> None:
    """Write repo files into sandbox and initialise git branch for candidates."""
    repo_files = _repo_files_from_structure(task.repo_structure)
    if not repo_files:
        return

    files_api = getattr(sandbox, "files", None)
    if not files_api or not hasattr(files_api, "write"):
        logger.warning("Sandbox files API unavailable; skipping repository materialization")
        return

    repo_root = _workspace_repo_root(task)

    sandbox.run_code(
        "import pathlib\n"
        f"pathlib.Path({repo_root!r}).mkdir(parents=True, exist_ok=True)\n"
    )

    for rel_path, content in repo_files:
        target_path = f"{repo_root}/{rel_path.lstrip('/')}"
        files_api.write(target_path, content)

    sandbox.run_code(
        "import pathlib, subprocess\n"
        f"repo = pathlib.Path({repo_root!r})\n"
        "subprocess.run(['git', 'init', '-b', 'candidate'], cwd=repo, check=False, capture_output=True)\n"
        "subprocess.run(['git', 'add', '.'], cwd=repo, check=False, capture_output=True)\n"
        "subprocess.run(['git', 'commit', '-m', 'Initial assessment context'], cwd=repo, check=False, capture_output=True)\n"
    )
    if not _ensure_workspace_repo_permissions(sandbox, repo_root):
        raise RuntimeError(f"Failed to normalize workspace permissions for {repo_root}")

    logger.info("Materialized %d repository files under %s", len(repo_files), repo_root)


def _is_demo_workspace_fallback_enabled(assessment: Assessment) -> bool:
    return bool(getattr(assessment, "is_demo", False))


def _run_workspace_bootstrap(
    e2b: Any,
    sandbox: Any,
    task: Task,
    repo_root: str,
) -> Dict[str, Any]:
    config = _task_extra_data(task).get("workspace_bootstrap") or {}
    if not isinstance(config, dict):
        return {"ran": False, "success": True, "must_succeed": False, "working_dir": repo_root, "steps": []}

    commands = [str(command).strip() for command in (config.get("commands") or []) if str(command or "").strip()]
    if not commands:
        return {"ran": False, "success": True, "must_succeed": False, "working_dir": repo_root, "steps": []}

    working_dir = str(config.get("working_dir") or repo_root).strip() or repo_root
    try:
        timeout_seconds = int(config.get("timeout_seconds") or 90)
    except (TypeError, ValueError):
        timeout_seconds = 90
    timeout_seconds = max(5, min(timeout_seconds, 900))
    must_succeed = bool(config.get("must_succeed"))

    steps: List[Dict[str, Any]] = []
    overall_success = True
    for command in commands:
        try:
            process = e2b.run_command(
                sandbox,
                command,
                cwd=working_dir,
                timeout=timeout_seconds,
            )
            stdout, stderr, exit_code = _extract_process_output(process)
            step_success = exit_code in (None, 0)
        except Exception as exc:
            stdout, stderr, exit_code = _extract_process_output(exc)
            logger.exception("Workspace bootstrap command failed task_id=%s", getattr(task, "id", None))
            if exit_code is None:
                stderr = ""
            step_success = False

        steps.append(
            {
                "command": command,
                "cwd": working_dir,
                "exit_code": exit_code,
                "success": step_success,
                "stdout_tail": _trim_bootstrap_output(stdout),
                "stderr_tail": _trim_bootstrap_output(stderr),
                "error_code": None if step_success else ("workspace_command_failed" if exit_code is None else "workspace_command_exit_nonzero"),
            }
        )
        if not step_success:
            overall_success = False
            break

    return {
        "ran": True,
        "success": overall_success,
        "must_succeed": must_succeed,
        "working_dir": working_dir,
        "steps": steps,
    }


def _collect_git_evidence_from_sandbox(sandbox: Any, repo_root: str) -> Dict[str, Any]:
    """Best-effort git evidence capture for evaluator context."""
    try:
        result = sandbox.run_code(
            "import json,subprocess,pathlib\n"
            f"repo=pathlib.Path({repo_root!r})\n"
            "MAX_CHARS = 50000\n"
            "def trim(txt):\n"
            "  text = (txt or '').strip()\n"
            "  return text[-MAX_CHARS:]\n"
            "def run(cmd):\n"
            "  p=subprocess.run(cmd,cwd=repo,capture_output=True,text=True)\n"
            "  return {'rc': p.returncode, 'stdout': trim(p.stdout), 'stderr': trim(p.stderr)}\n"
            "payload={\n"
            " 'head_sha': None,\n"
            " 'status_porcelain': '',\n"
            " 'diff_main': '',\n"
            " 'diff_staged': '',\n"
            " 'commits': '',\n"
            " 'diff_base_ref': None,\n"
            "}\n"
            "if not repo.exists():\n"
            "  payload['error'] = 'repo_root_missing'\n"
            "else:\n"
            "  probe = run(['git','rev-parse','--is-inside-work-tree'])\n"
            "  if probe['rc'] != 0 or probe['stdout'].lower() != 'true':\n"
            "    payload['error'] = 'not_a_git_repository'\n"
            "    if probe['stderr']:\n"
            "      payload['git_probe_error'] = 'git_probe_failed'\n"
            "  else:\n"
            "    head = run(['git','rev-parse','HEAD'])\n"
            "    payload['head_sha'] = head['stdout'] or None\n"
            "    status = run(['git','status','--porcelain'])\n"
            "    payload['status_porcelain'] = status['stdout']\n"
            "    staged = run(['git','diff','--cached'])\n"
            "    payload['diff_staged'] = staged['stdout']\n"
            "    log = run(['git','log','--oneline','--decorate','-n','50'])\n"
            "    payload['commits'] = log['stdout']\n"
            "    base_ref = None\n"
            "    for ref in ('origin/main','main','origin/master','master'):\n"
            "      check = run(['git','rev-parse','--verify',ref])\n"
            "      if check['rc'] == 0:\n"
            "        base_ref = ref\n"
            "        break\n"
            "    if base_ref:\n"
            "      diff = run(['git','diff',f'{base_ref}...HEAD'])\n"
            "      payload['diff_main'] = diff['stdout']\n"
            "      payload['diff_base_ref'] = base_ref\n"
            "      if diff['rc'] != 0 and not payload['diff_main'] and diff['stderr']:\n"
            "        payload['diff_main_error'] = 'git_diff_failed'\n"
            "    if not payload['diff_main']:\n"
            "      fallback = run(['git','diff','HEAD~1','HEAD'])\n"
            "      if fallback['rc'] == 0:\n"
            "        payload['diff_main'] = fallback['stdout']\n"
            "        payload['diff_base_ref'] = payload['diff_base_ref'] or 'HEAD~1'\n"
            "    if not payload['diff_main'] and payload['status_porcelain']:\n"
            "      worktree = run(['git','diff'])\n"
            "      if worktree['rc'] == 0:\n"
            "        payload['diff_main'] = worktree['stdout']\n"
            "        payload['diff_base_ref'] = payload['diff_base_ref'] or 'WORKTREE'\n"
            "print(json.dumps(payload))\n"
        )
        out = _execution_stdout_text(result).strip().splitlines()
        if out:
            payload = json.loads(out[-1])
            payload.setdefault("head_sha", None)
            payload.setdefault("status_porcelain", "")
            payload.setdefault("diff_main", "")
            payload.setdefault("diff_staged", "")
            payload.setdefault("commits", "")
            payload.setdefault("diff_base_ref", None)
            return payload
    except Exception:
        logger.exception("Failed to capture git evidence from sandbox")
    return {
        "head_sha": None,
        "status_porcelain": "",
        "diff_main": "",
        "diff_staged": "",
        "commits": "",
        "diff_base_ref": None,
    }


def _auto_submit_on_timeout(assessment: Assessment, task: Task, db: Session) -> None:
    """Compatibility facade for the former unscored timeout shortcut."""

    del task
    finalize_timed_out_assessment(assessment, db)


def enforce_active_or_timeout(
    assessment: Assessment,
    db: Session,
    *,
    workspace_lock_held: bool = False,
) -> None:
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        return
    if time_remaining_seconds(assessment) > 0:
        return
    result = finalize_timed_out_assessment(
        assessment,
        db,
        workspace_lock_held=workspace_lock_held,
    )
    raise HTTPException(
        status_code=409,
        detail=timeout_finalization_http_detail(result),
    )


def timeout_finalization_http_detail(result: Any) -> str | dict[str, str]:
    """Describe the authoritative timeout outcome without claiming false success."""

    disposition = result if isinstance(result, dict) else {}
    status_value = str(disposition.get("status") or "")
    if status_value == "finalized":
        return "Assessment time expired and was auto-submitted"
    if status_value == "already_submitted":
        return "Assessment time expired; the assessment was already submitted"
    if status_value == "blocked":
        return {
            "code": "ASSESSMENT_TIMEOUT_RECONCILIATION_REQUIRED",
            "message": (
                "Assessment time expired, but an AI request must be reconciled "
                "before the current workspace can be graded"
            ),
        }
    return {
        "code": "ASSESSMENT_TIMEOUT_FINALIZATION_PENDING",
        "message": (
            "Assessment time expired, but automatic submission did not complete; "
            "refresh before retrying"
        ),
    }


def finalize_timed_out_assessment(
    assessment: Assessment,
    db: Session,
    *,
    workspace_lock_held: bool = False,
) -> Dict[str, Any]:
    """Capture + score an IN_PROGRESS assessment whose working timer has expired
    but whose candidate never submitted (closed the tab / walked away).

    This is the server-side backstop for ``enforce_active_or_timeout``, which is
    *pull-based*: it only finalizes when the candidate makes another request. A
    candidate who works then abandons therefore leaves the row IN_PROGRESS until a
    reaper marks it EXPIRED — historically *discarding the work*. Here we run the
    real submit pipeline so the effort is captured, scored (best-effort — the E2B
    sandbox may have lapsed), and surfaced to the recruiter as a
    COMPLETED_DUE_TO_TIMEOUT result instead of vanishing.

    Mirrors the recruiter Rescore path (``rescore_assessment``): the submit
    pipeline's atomic claim flips the row terminal BEFORE any sandbox call, so even
    if scoring hard-fails the assessment still ends terminal (never re-discovered,
    never discarded) with ``scoring_failed`` set for a later manual rescore.

    Idempotent: a row already taken to a terminal state (e.g. by a racing
    candidate submit) is skipped.
    """
    assessment_id = int(assessment.id)
    if not workspace_lock_held:
        prepare_assessment_workspace_mutex(db)
    lock = (
        nullcontext()
        if workspace_lock_held
        else assessment_workspace_mutex(db, assessment_id=assessment_id)
    )
    with lock:
        return _finalize_timed_out_assessment_serialized(assessment, db)


def _finalize_timed_out_assessment_serialized(
    assessment: Assessment,
    db: Session,
) -> Dict[str, Any]:
    """Canonical timeout completion while the workspace mutex is owned."""

    assessment_id = int(assessment.id)
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .populate_existing()
        .with_for_update(of=Assessment)
        .one_or_none()
    )
    if assessment is None:
        db.rollback()
        return {
            "status": "skipped",
            "reason": "not_found",
            "assessment_id": assessment_id,
        }
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        db.rollback()
        return {
            "status": "skipped",
            "reason": "not_in_progress",
            "assessment_id": assessment_id,
        }
    if time_remaining_seconds(assessment) > 0:
        db.rollback()
        return {
            "status": "skipped",
            "reason": "time_remaining",
            "assessment_id": assessment_id,
        }

    from .candidate_chat_submission import (
        finalize_or_block_candidate_chat_for_submit,
    )

    try:
        finalize_or_block_candidate_chat_for_submit(
            db,
            assessment_id=assessment_id,
            token=str(assessment.token),
            close_in_doubt_without_replay=True,
        )
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == 409:
            return {
                "status": "blocked",
                "reason": "chat_reconciliation_required",
                "assessment_id": assessment_id,
            }
        raise
    assessment = (
        db.query(Assessment)
        .filter(Assessment.id == assessment_id)
        .populate_existing()
        .one()
    )

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    final_code = resume_code_for_assessment(assessment, (getattr(task, "starter_code", "") or ""))

    scoring_failed = False
    try:
        # Defer the agent wake until after the timeout-specific terminal state is
        # committed below.  Otherwise this path would enqueue once as a normal
        # COMPLETED submission and again as COMPLETED_DUE_TO_TIMEOUT.
        submit_assessment(
            assessment,
            final_code,
            int(assessment.tab_switch_count or 0),
            db,
            wake_agent_on_commit=False,
            # Relabel the row as COMPLETED_DUE_TO_TIMEOUT first, then dispatch
            # retry. This avoids a fast worker racing the timeout commit and
            # emitting the post-assessment wake twice.
            enqueue_rubric_retry_on_commit=False,
            workspace_lock_held=True,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            # A racing candidate submit won the atomic claim — their real
            # submission stands; don't relabel it as a timeout.
            db.rollback()
            return {"status": "already_submitted", "assessment_id": assessment.id}
        db.rollback()
        scoring_failed = True
        logger.warning(
            "Timed-out finalize: scoring failed assessment_id=%s detail=%s",
            assessment.id, getattr(exc, "detail", exc),
        )
    except Exception:
        db.rollback()
        scoring_failed = True
        logger.exception("Timed-out finalize: scoring crashed assessment_id=%s", assessment.id)

    # Relabel terminal as the (more honest) timeout completion. submit set
    # COMPLETED on the happy path and the atomic claim set it on most failures;
    # force it here too so a pre-claim failure is never left in limbo/discarded.
    assessment.status = AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assessment.completed_due_to_timeout = True
    if not assessment.completed_at:
        assessment.completed_at = utcnow()
    if scoring_failed:
        assessment.scoring_failed = True
    append_assessment_timeline_event(
        assessment, "auto_submit_timeout_sweep", {"scoring_failed": scoring_failed}
    )
    db.commit()
    grading_incomplete = bool(
        getattr(assessment, "scoring_failed", False)
        or getattr(assessment, "scoring_partial", False)
    )
    if grading_incomplete:
        try:
            from ...tasks.rubric_retry_tasks import retry_incomplete_rubric_scoring

            retry_incomplete_rubric_scoring.delay(int(assessment.id))
        except Exception:
            logger.exception(
                "Failed to enqueue timeout rubric retry assessment_id=%s; sweep will recover",
                assessment.id,
            )
    if not grading_incomplete:
        _wake_role_agent_after_assessment(assessment)
    return {"status": "finalized", "assessment_id": assessment.id, "scoring_failed": scoring_failed}


def pause_assessment_timer(assessment: Assessment, pause_reason: str, db: Session) -> None:
    if assessment.is_timer_paused:
        return
    assessment.is_timer_paused = True
    assessment.paused_at = utcnow()
    assessment.pause_reason = pause_reason
    append_assessment_timeline_event(
        assessment,
        "timer_paused",
        {"pause_reason": pause_reason},
    )
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit assessment timer pause assessment_id=%s", assessment.id)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to pause assessment timer")


def resume_assessment_timer(assessment: Assessment, db: Session, resume_reason: str = "manual_retry") -> None:
    if not assessment.is_timer_paused:
        return
    now = utcnow()
    paused_at = ensure_utc(assessment.paused_at)
    paused_for_seconds = 0
    if paused_at is not None:
        paused_for_seconds = max(0, int((now - paused_at).total_seconds()))
    assessment.total_paused_seconds = int(assessment.total_paused_seconds or 0) + paused_for_seconds
    assessment.is_timer_paused = False
    assessment.paused_at = None
    assessment.pause_reason = None
    append_assessment_timeline_event(
        assessment,
        "timer_resumed",
        {"resume_reason": resume_reason, "paused_for_seconds": paused_for_seconds},
    )
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit assessment timer resume assessment_id=%s", assessment.id)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to resume assessment timer")

# ---------------------------------------------------------------------------
# CV upload
# ---------------------------------------------------------------------------

def store_cv_upload(assessment: Assessment, upload: UploadFile, db: Session) -> Dict[str, Any]:
    result = process_document_upload(
        upload=upload,
        entity_id=assessment.id,
        doc_type="cv",
        allowed_extensions={"pdf", "docx"},
    )

    # Store on the assessment (audit trail)
    assessment.cv_file_url = result["file_url"]
    assessment.cv_filename = result["filename"]
    assessment.cv_uploaded_at = utcnow()

    # Also store extracted text on the candidate (for CV-job matching)
    if assessment.candidate_id:
        candidate = db.query(Candidate).filter(Candidate.id == assessment.candidate_id).first()
        if candidate:
            candidate.cv_file_url = result["file_url"]
            candidate.cv_filename = result["filename"]
            candidate.cv_text = result["extracted_text"]
            candidate.cv_uploaded_at = utcnow()

    try:
        db.commit()
    except Exception:
        logger.exception("Failed to commit CV metadata assessment_id=%s", assessment.id)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to store CV metadata")

    return {
        "success": True,
        "assessment_id": assessment.id,
        "cv_filename": assessment.cv_filename,
        "cv_uploaded_at": assessment.cv_uploaded_at,
        "text_extracted": bool(result["extracted_text"]),
    }


# ---------------------------------------------------------------------------
# Start / resume
# ---------------------------------------------------------------------------


def get_assessment_creation_gate(
    organization_id: int,
    db: Session,
    *,
    role_id: int | None = None,
    exclude_assessment_id: int | None = None,
    lock_organization: bool = False,
) -> Dict[str, Any]:
    """Return whether an org can create another assessment invite."""
    org_query = db.query(Organization).filter(Organization.id == organization_id)
    if lock_organization:
        org_query = org_query.with_for_update()
    org = org_query.first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    credits_balance = int(org.credits_balance or 0)

    # Usage-based gate: in shadow mode (USAGE_METER_LIVE=False) we never
    # block assessment creation — Claude calls still record events but the
    # ledger isn't debited, so balance can stay at the free-tier grant
    # indefinitely. In live mode we reject when the org's balance can't
    # cover the per-assessment reservation estimate.
    from ...services.pricing_service import Feature
    from ...services.usage_metering_service import (
        InsufficientCreditsError,
        reserve as _meter_reserve,
    )
    from ...services.usage_credit_reservations import (
        InsufficientRoleBudgetError,
        ensure_role_capacity,
    )

    try:
        reservation = _meter_reserve(
            db, organization_id=organization_id, feature=Feature.ASSESSMENT
        )
    except InsufficientCreditsError as exc:
        return {
            "can_create": False,
            "reason": "insufficient_credits",
            "message": ORG_INSUFFICIENT_CREDITS_MESSAGE,
            "organization": org,
            "credits_balance": credits_balance,
            "reserved_pending_assessments": 0,
            "remaining_capacity": credits_balance - exc.required,
        }

    if role_id is not None:
        try:
            ensure_role_capacity(
                db,
                organization_id=int(organization_id),
                role_id=int(role_id),
                required=int(reservation),
            )
        except InsufficientRoleBudgetError as exc:
            return {
                "can_create": False,
                "reason": "role_monthly_budget_insufficient",
                "message": ROLE_BUDGET_EXHAUSTED_MESSAGE,
                "organization": org,
                "credits_balance": credits_balance,
                "reserved_pending_assessments": 0,
                "remaining_capacity": int(exc.available),
            }

    return {
        "can_create": True,
        "reason": None,
        "message": None,
        "organization": org,
        "credits_balance": credits_balance,
        "reserved_pending_assessments": 0,
        "remaining_capacity": credits_balance - reservation,
    }


def get_assessment_start_gate(
    assessment: Assessment,
    db: Session,
    *,
    lock_organization: bool = False,
) -> Dict[str, Any]:
    """Return whether a candidate can begin the assessment right now."""
    was_pending = assessment.status == AssessmentStatus.PENDING
    is_demo = bool(getattr(assessment, "is_demo", False))
    # Demo assessments and resumed-in-progress assessments don't gate on
    # fresh-call capacity. The role ceiling remains active in shadow-meter
    # mode because UsageEvents (and therefore monthly spend) still accrue.
    if not was_pending or is_demo:
        return {"can_start": True, "reason": None, "message": None, "organization": None}

    org = None
    if settings.USAGE_METER_LIVE:
        org_query = db.query(Organization).filter(
            Organization.id == assessment.organization_id
        )
        if lock_organization:
            org_query = org_query.with_for_update()
        org = org_query.first()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        if (
            getattr(assessment, "credit_consumed_at", None) is None
            and int(org.credits_balance or 0) <= 0
        ):
            return {
                "can_start": False,
                "reason": "insufficient_credits",
                "message": CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE,
                "organization": org,
            }

    role_id = getattr(assessment, "role_id", None)
    if role_id is not None:
        from ...services.pricing_service import Feature, estimate_reservation
        from ...services.usage_credit_reservations import (
            InsufficientRoleBudgetError,
            ensure_role_capacity,
        )

        try:
            ensure_role_capacity(
                db,
                organization_id=int(assessment.organization_id),
                role_id=int(role_id),
                required=int(estimate_reservation(Feature.ASSESSMENT)),
            )
        except InsufficientRoleBudgetError:
            return {
                "can_start": False,
                "reason": "role_monthly_budget_insufficient",
                "message": CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE,
                "organization": org,
            }

    return {"can_start": True, "reason": None, "message": None, "organization": org}


def start_or_resume_assessment(
    assessment: Assessment,
    db: Session,
) -> Dict[str, Any]:
    """Start or resume using short DB claims around detached provider work."""
    from ...domains.assessments_runtime.assessment_start_boundary import (
        start_or_resume_assessment_impl,
    )

    return start_or_resume_assessment_impl(assessment, db)


def _persist_post_claim_scoring_failure(
    assessment_id: int,
    db: Session,
) -> Assessment | None:
    """Fail closed after the terminal submission claim has committed.

    Sandbox reconnect, test execution, git push, and provider scoring all run
    after the atomic IN_PROGRESS -> COMPLETED claim. Any failure in that region
    must leave a durable retry outbox row; otherwise the candidate cannot submit
    again and the recovery sweep cannot see the stranded result.
    """
    try:
        db.rollback()
        row = (
            db.query(Assessment)
            .filter(Assessment.id == int(assessment_id))
            .one_or_none()
        )
        if row is None or row.status not in {
            AssessmentStatus.COMPLETED,
            AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
        }:
            return None

        breakdown = (
            dict(row.score_breakdown)
            if isinstance(getattr(row, "score_breakdown", None), dict)
            else {}
        )
        task = db.query(Task).filter(Task.id == row.task_id).one_or_none()
        failed_dimension_ids = list(
            (task.evaluation_rubric or {}).keys()
            if task is not None and isinstance(task.evaluation_rubric, dict)
            else []
        )
        rubric = (
            dict(breakdown.get("rubric_grading"))
            if isinstance(breakdown.get("rubric_grading"), dict)
            else {}
        )
        rubric.update(
            {
                "status": "failed",
                "fully_graded": False,
                "failed_dimension_ids": failed_dimension_ids,
                "error": "submission_pipeline_failed",
            }
        )
        retry = (
            dict(rubric.get("retry"))
            if isinstance(rubric.get("retry"), dict)
            else {}
        )
        retry.update(
            {
                "status": "pending",
                "attempt_count": max(0, int(retry.get("attempt_count") or 0)),
                "next_attempt_at": utcnow().isoformat(),
                "last_error": "submission_pipeline_failed",
            }
        )
        rubric["retry"] = retry
        breakdown["rubric_grading"] = rubric
        breakdown["scoring_failure"] = {
            "status": "retrying",
            "stage": "post_claim_submission",
            "error_code": "submission_pipeline_failed",
            "occurred_at": utcnow().isoformat(),
        }
        row.score_breakdown = breakdown
        row.scoring_failed = True
        row.scoring_partial = False
        row.score = None
        row.final_score = None
        row.assessment_score = None
        row.taali_score = None
        row.scored_at = None
        if not row.completed_at:
            row.completed_at = utcnow()
        append_assessment_timeline_event(
            row,
            "assessment_scoring_failed",
            {
                "stage": "post_claim_submission",
                "error_code": "submission_pipeline_failed",
                "automatic_retry": True,
            },
        )
        if row.application_id:
            app = (
                db.query(CandidateApplication)
                .filter(CandidateApplication.id == row.application_id)
                .one_or_none()
            )
            if app is not None:
                refresh_application_score_cache(app, db=db)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to persist post-claim scoring failure assessment_id=%s",
            assessment_id,
        )
        return None


def submit_assessment(
    assessment: Assessment,
    final_code: str,
    tab_switch_count: int,
    db: Session,
    *,
    wake_agent_on_commit: bool = True,
    retry_scoring: bool = False,
    suppress_completion_side_effects: bool = False,
    enqueue_rubric_retry_on_commit: bool = True,
    workspace_lock_held: bool = False,
) -> Dict[str, Any]:
    try:
        result = submit_assessment_impl(
            assessment,
            final_code,
            tab_switch_count,
            db,
            settings_obj=settings,
            e2b_service_cls=E2BService,
            workspace_repo_root_fn=_workspace_repo_root,
            collect_git_evidence_fn=_collect_git_evidence_from_sandbox,
            recover_retry_sandbox_fn=_recover_retry_sandbox_from_pushed_branch,
            retry_scoring=retry_scoring,
            suppress_completion_side_effects=suppress_completion_side_effects,
            enqueue_rubric_retry_on_commit=enqueue_rubric_retry_on_commit,
            workspace_lock_held=workspace_lock_held,
        )
    except Exception as exc:
        # 400 is a pre-claim lifecycle rejection. 409 means a racing submitter
        # won the claim and may still be actively scoring; never poison its row.
        pre_claim_rejection = isinstance(exc, HTTPException) and exc.status_code in {
            400,
            409,
        }
        recovered = None
        if not pre_claim_rejection:
            logger.error("Post-claim assessment scoring failed assessment_id=%s", getattr(assessment, "id", None), exc_info=True)
            recovered = _persist_post_claim_scoring_failure(
                int(assessment.id),
                db,
            )
        if (
            recovered is not None
            and not retry_scoring
            and enqueue_rubric_retry_on_commit
        ):
            try:
                from ...tasks.rubric_retry_tasks import retry_incomplete_rubric_scoring

                retry_incomplete_rubric_scoring.delay(int(recovered.id))
            except Exception:
                logger.exception(
                    "Failed to enqueue post-claim scoring retry assessment_id=%s; sweep will recover",
                    recovered.id,
                )
        raise
    # ``submit_assessment_impl`` returns only after its score/pipeline commit.
    # Wake the complete role cohort pipeline now so an enabled agent can act on
    # the fresh result immediately instead of waiting for the periodic sweep.
    if wake_agent_on_commit and result.get("grading_status") != "pending":
        _wake_role_agent_after_assessment(assessment)
    return result


def _wake_role_agent_after_assessment(assessment: Assessment) -> bool:
    """Best-effort, bounded wake-up for the assessment's role agent.

    The cohort task owns the canonical enabled/paused and concurrent-run guards,
    plus idempotent scoring/decision materialisation.  This hook therefore does
    exactly one dispatch when a role is present and never changes the already
    committed submission outcome if the broker is unavailable.
    """
    role_id = getattr(assessment, "role_id", None)
    if role_id is None:
        return False
    try:
        from ...tasks.agent_tasks import agent_cohort_tick_role

        agent_cohort_tick_role.delay(int(role_id), activation=False)
        return True
    except Exception:
        logger.exception(
            "Failed to enqueue post-assessment agent cycle assessment_id=%s role_id=%s",
            getattr(assessment, "id", None),
            role_id,
        )
        return False
