"""Assessment business logic: start, submit, CV upload, scoring orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...platform.config import settings
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.organization import Organization
from ...models.candidate_application import CandidateApplication
from ...models.task import Task
from ...components.integrations.e2b.service import E2BService
from ...services.document_service import process_document_upload
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.credit_ledger_service import append_credit_ledger_entry
from ...services.candidate_cv_input_lifecycle import replace_candidate_cv_and_invalidate
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root
from ...services.task_repo_service import normalize_repo_files
from ...services.task_battle_test import reconstruct_generated_task_spec
from ...services.task_spec_loader import (
    TaskSpecValidationMode,
    candidate_rubric_view,
    validate_task_spec,
)
from ...domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from ...domains.assessments_runtime.role_support import refresh_application_score_cache
from .claude_budget import build_claude_budget_snapshot, resolve_effective_budget_limit_usd
from .interrogation import render_opener
from .submission_runtime import (
    submit_assessment_impl,
)
from .task_snapshot import freeze_assessment_task, task_view_for_assessment
from .terminal_runtime import resolve_ai_mode, terminal_capabilities
from .workspace_provisioning import (
    _ensure_workspace_repo_permissions,
    _execution_stdout_text,
    _materialize_task_repository as _materialize_task_repository_impl,
    _repo_files_from_structure,
    _sandbox_operation_payload,
    _sandbox_operation_succeeded,
    _workspace_repo_root,
)
from .repository import (
    utcnow,
    ensure_utc,
    resume_code_for_assessment,
    append_assessment_timeline_event,
    time_remaining_seconds,
)

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


def _task_extra_data(task: Task) -> Dict[str, Any]:
    extra = getattr(task, "extra_data", None)
    return extra if isinstance(extra, dict) else {}


def _enforce_artifact_first_task(task: Task) -> None:
    """Refuse to start a new assessment on a legacy/chat-dominant task."""
    result = validate_task_spec(
        reconstruct_generated_task_spec(task),
        mode=TaskSpecValidationMode.PUBLICATION,
    )
    errors = result.errors
    if not errors:
        return
    logger.error(
        "Assessment task failed publication contract task_id=%s errors=%s",
        getattr(task, "id", None),
        errors,
    )
    raise HTTPException(
        status_code=503,
        detail="This assessment task needs an update before it can be started. Please contact the hiring team.",
    )


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


def _trim_bootstrap_output(text: str, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _clone_assessment_branch_into_workspace(sandbox: Any, assessment: Assessment, task: Task) -> bool:
    """Provision a candidate-safe local worktree.

    The legacy function name is retained for callers while candidate-side Git
    clones are removed. Repository URLs, platform credentials, and persistent
    assessment refs are deliberately never passed into the sandbox.
    """

    try:
        _materialize_task_repository(sandbox, task)
        return True
    except Exception:
        logger.exception(
            "Failed to provision local-only candidate workspace assessment=%s",
            getattr(assessment, "id", None),
        )
        return False


def _sandbox_workspace_is_ready(sandbox: Any, task: Task) -> bool:
    """Verify that an existing workspace is the completed local Git worktree.

    Resuming an assessment must never recreate the task baseline over a
    candidate's live work. If the provider cannot reconnect the original
    sandbox, callers fail closed instead of silently replacing the workspace.

    A directory alone is not a readiness signal: provisioning can fail after
    creating the root but before Git initialization finishes.  The durable
    server-side readiness signal is the assessment's IN_PROGRESS + sandbox-id
    commit, which is written only after this worktree and the required
    bootstrap complete.  On reconnect we additionally validate the worktree
    perimeter without modifying it.
    """
    repo_root = _workspace_repo_root(task)
    try:
        result = sandbox.run_code(
            "import json, os, pathlib, shutil, stat, subprocess\n"
            f"root = pathlib.Path({repo_root!r})\n"
            "payload = {'ready': False, 'reason': 'workspace_missing'}\n"
            "trusted_path = '/usr/local/bin:/usr/bin:/bin'\n"
            "git_env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}\n"
            "git_env.update({'PATH': trusted_path, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'})\n"
            "def run(args):\n"
            "  return subprocess.run(args, cwd=root, env=git_env, check=False, capture_output=True, text=True)\n"
            "try:\n"
            "  item_stat = root.lstat()\n"
            "  git_dir = root / '.git'\n"
            "  git_stat = git_dir.lstat()\n"
            "  root_safe = stat.S_ISDIR(item_stat.st_mode) and not stat.S_ISLNK(item_stat.st_mode)\n"
            "  git_safe = stat.S_ISDIR(git_stat.st_mode) and not stat.S_ISLNK(git_stat.st_mode)\n"
            "  git_binary = shutil.which('git', path=trusted_path)\n"
            "  if root_safe and git_safe and git_binary:\n"
            "    inside = run([git_binary, 'rev-parse', '--is-inside-work-tree'])\n"
            "    head = run([git_binary, 'rev-parse', '--verify', 'HEAD'])\n"
            "    remotes = run([git_binary, 'remote'])\n"
            "    remote_refs = run([git_binary, 'for-each-ref', '--format=%(refname)', 'refs/remotes'])\n"
            "    payload['ready'] = (\n"
            "      inside.returncode == 0 and (inside.stdout or '').strip().lower() == 'true'\n"
            "      and head.returncode == 0 and bool((head.stdout or '').strip())\n"
            "      and remotes.returncode == 0 and not (remotes.stdout or '').strip()\n"
            "      and remote_refs.returncode == 0 and not (remote_refs.stdout or '').strip()\n"
            "    )\n"
            "    payload['reason'] = 'ready' if payload['ready'] else 'git_perimeter_invalid'\n"
            "  else:\n"
            "    payload['reason'] = 'unsafe_workspace_root'\n"
            "except (FileNotFoundError, OSError):\n"
            "  pass\n"
            "print(json.dumps(payload))\n"
        )
        return _sandbox_operation_payload(result).get("ready") is True
    except Exception:
        logger.exception("Failed to verify candidate workspace root=%s", repo_root)
        return False


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
    """Compatibility seam for callers that patch service-level helpers."""
    _materialize_task_repository_impl(
        sandbox,
        task,
        repo_files_from_structure_fn=_repo_files_from_structure,
        workspace_repo_root_fn=_workspace_repo_root,
        sandbox_operation_succeeded_fn=_sandbox_operation_succeeded,
        ensure_workspace_repo_permissions_fn=_ensure_workspace_repo_permissions,
    )


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
            if exit_code is None and not stderr:
                stderr = str(exc)
            step_success = False

        steps.append(
            {
                "command": command,
                "cwd": working_dir,
                "exit_code": exit_code,
                "success": step_success,
                "stdout_tail": _trim_bootstrap_output(stdout),
                "stderr_tail": _trim_bootstrap_output(stderr),
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
            "      payload['git_probe_stderr'] = probe['stderr']\n"
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
            "        payload['diff_main_error'] = diff['stderr']\n"
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


def enforce_active_or_timeout(assessment: Assessment, db: Session) -> None:
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        return
    if time_remaining_seconds(assessment) > 0:
        return
    # Use the same lease + immutable-artifact submission path as an explicit
    # candidate submit. The legacy direct timeout writer could mark the row
    # terminal even when artifact capture failed.
    result = finalize_timed_out_assessment(assessment, db)
    if result.get("status") == "capture_failed":
        raise HTTPException(
            status_code=409,
            detail="Assessment time expired; auto-submitted work capture is being securely retried",
        )
    raise HTTPException(status_code=409, detail="Assessment time expired and was auto-submitted")


def finalize_timed_out_assessment(assessment: Assessment, db: Session) -> Dict[str, Any]:
    """Freeze an expired live workspace and queue its artifact for grading.

    Capture failure stays retryable; a racing completed submission is skipped.
    """
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        return {"status": "skipped", "reason": "not_in_progress", "assessment_id": assessment.id}

    live_task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not live_task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        task = task_view_for_assessment(assessment, live_task)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="This assessment's task definition could not be verified. Please contact the hiring team.",
        ) from exc
    final_code = resume_code_for_assessment(assessment, (getattr(task, "starter_code", "") or ""))

    scoring_failed = False
    try:
        submit_assessment(
            assessment,
            final_code,
            int(assessment.tab_switch_count or 0),
            db,
            wake_agent_on_commit=False,
            defer_scoring=True,
            enqueue_rubric_retry_on_commit=False,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            db.rollback()
            return {"status": "already_submitted", "assessment_id": assessment.id}
        db.rollback()
        current_status = (
            db.query(Assessment.status)
            .filter(Assessment.id == int(assessment.id))
            .scalar()
        )
        if current_status == AssessmentStatus.IN_PROGRESS:
            db.refresh(assessment)
            assessment.scoring_failed = True
            append_assessment_timeline_event(
                assessment,
                "auto_submit_timeout_capture_failed",
                {"error": str(getattr(exc, "detail", exc))[:500]},
            )
            db.commit()
            return {
                "status": "capture_failed",
                "assessment_id": assessment.id,
                "scoring_failed": True,
            }
        scoring_failed = True
        logger.warning(
            "Timed-out finalize: artifact capture failed assessment_id=%s detail=%s",
            assessment.id, getattr(exc, "detail", exc),
        )
    except Exception:
        db.rollback()
        current_status = (
            db.query(Assessment.status)
            .filter(Assessment.id == int(assessment.id))
            .scalar()
        )
        if current_status == AssessmentStatus.IN_PROGRESS:
            db.refresh(assessment)
            assessment.scoring_failed = True
            append_assessment_timeline_event(
                assessment,
                "auto_submit_timeout_capture_failed",
                {"error": "submission_capture_unavailable"},
            )
            db.commit()
            logger.exception(
                "Timed-out finalize: artifact capture crashed assessment_id=%s",
                assessment.id,
            )
            return {
                "status": "capture_failed",
                "assessment_id": assessment.id,
                "scoring_failed": True,
            }
        scoring_failed = True
        logger.exception("Timed-out finalize: artifact capture crashed assessment_id=%s", assessment.id)

    # Relabel the successfully frozen terminal row as the more honest timeout
    # completion. Pre-terminal capture failures returned above stay retryable
    # for the next sweep and retain their live source sandbox.
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


def enforce_not_paused(assessment: Assessment) -> None:
    if getattr(assessment, "is_timer_paused", False):
        raise HTTPException(
            status_code=423,
            detail={
                "code": "ASSESSMENT_PAUSED",
                "message": "Assessment is paused while AI assistant is unavailable",
                "pause_reason": getattr(assessment, "pause_reason", None),
            },
        )


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
    assessment.cv_text_snapshot = result["extracted_text"]

    # Also store extracted text on the candidate (for CV-job matching)
    if assessment.candidate_id:
        replace_candidate_cv_and_invalidate(
            db,
            candidate_id=int(assessment.candidate_id),
            organization_id=int(assessment.organization_id),
            upload_result=result,
            uploaded_at=utcnow(),
            reason="assessment_cv_changed",
        )

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
    """Start a new assessment or resume an in-progress one. Returns AssessmentStart payload.

    The ``calibration_warmup_prompt`` arg was removed when the separate warmup
    scoring path was dropped — the in-session prompts produce the same
    ``prompt_clarity`` signal across every real prompt.
    """
    if assessment.status not in {
        AssessmentStatus.PENDING,
        AssessmentStatus.IN_PROGRESS,
    }:
        raise HTTPException(status_code=400, detail="Assessment has already ended")
    if assessment.expires_at and ensure_utc(assessment.expires_at) < utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")
    if not (settings.E2B_API_KEY or "").strip():
        raise HTTPException(status_code=503, detail="Code environment is not configured. Please try again later.")
    try:
        required_ai_mode = resolve_ai_mode()
    except Exception as exc:
        logger.exception("Assessment AI runtime is not configured")
        raise HTTPException(status_code=503, detail="The assessment isn't available right now. Please try again later.") from exc

    was_pending = assessment.status == AssessmentStatus.PENDING
    if not was_pending and (
        not assessment.started_at or not assessment.e2b_session_id
    ):
        # Never "repair" an IN_PROGRESS row by starting its clock now or by
        # creating a replacement sandbox. Either action can overwrite or
        # detach candidate work from the assessment record.
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The existing workspace session is incomplete. Please contact the hiring team; no replacement workspace was created.",
        )

    sandbox = None
    sandbox_id = None
    created_sandbox = False
    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        snapshot_created = freeze_assessment_task(assessment, task)
        task = task_view_for_assessment(assessment, task)
    except RuntimeError as exc:
        logger.exception(
            "Assessment task snapshot verification failed assessment_id=%s",
            assessment.id,
        )
        raise HTTPException(
            status_code=503,
            detail="This assessment's task definition could not be verified. Please contact the hiring team.",
        ) from exc
    if snapshot_created:
        append_assessment_timeline_event(
            assessment,
            "task_spec_frozen",
            {"sha256": assessment.task_spec_snapshot_sha256},
        )
    if was_pending:
        _enforce_artifact_first_task(task)
    start_gate = get_assessment_start_gate(assessment, db, lock_organization=True)
    if not start_gate.get("can_start"):
        raise HTTPException(status_code=402, detail=INSUFFICIENT_CREDITS_DETAIL)
    try:
        e2b = E2BService(settings.E2B_API_KEY)
        if not was_pending:
            try:
                sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
            except Exception as exc:
                logger.warning(
                    "Could not reconnect existing candidate sandbox assessment_id=%s",
                    assessment.id,
                )
                raise HTTPException(
                    status_code=503,
                    detail="The existing workspace could not be reconnected. Please retry; no replacement workspace was created.",
                ) from exc
        else:
            sandbox = e2b.create_sandbox()
            created_sandbox = True
        sandbox_id = e2b.get_sandbox_id(sandbox)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        if created_sandbox and sandbox is not None:
            try:
                e2b.close_sandbox(sandbox)
            except Exception:
                logger.exception(
                    "Failed to close E2B sandbox after environment startup failure assessment_id=%s",
                    assessment.id,
                )
        import logging as _logging
        _logging.getLogger("taali.assessments").exception("Could not start code environment")
        raise HTTPException(status_code=503, detail="Could not start code environment. Please try again later.")

    # A first start remains a PENDING database transaction until the local-only
    # worktree and every required offline bootstrap step have succeeded. This
    # makes retry safe: a failed sandbox is closed and no timer/session pointer
    # exists for a half-created workspace.
    bootstrap_result: Dict[str, Any] = {
        "ran": False,
        "success": True,
        "must_succeed": False,
    }
    if was_pending:
        try:
            if not _clone_assessment_branch_into_workspace(sandbox, assessment, task):
                raise RuntimeError("local workspace provisioning failed")
            bootstrap_result = _run_workspace_bootstrap(
                e2b,
                sandbox,
                task,
                _workspace_repo_root(task),
            )
            if bootstrap_result.get("ran"):
                append_assessment_timeline_event(
                    assessment,
                    "workspace_bootstrap",
                    {
                        "success": bool(bootstrap_result.get("success")),
                        "must_succeed": bool(bootstrap_result.get("must_succeed")),
                        "working_dir": bootstrap_result.get("working_dir"),
                        "steps": bootstrap_result.get("steps") or [],
                    },
                )
            if (
                bootstrap_result.get("must_succeed")
                and not bootstrap_result.get("success")
            ):
                raise RuntimeError("required workspace bootstrap failed")
        except Exception as exc:
            logger.exception(
                "Failed to prepare candidate workspace assessment_id=%s",
                assessment.id,
            )
            db.rollback()
            try:
                e2b.close_sandbox(sandbox)
            except Exception:
                logger.exception(
                    "Failed to close E2B sandbox after workspace preparation failure assessment_id=%s",
                    assessment.id,
                )
            detail = (
                "Failed to initialize assessment repository"
                if str(exc) == "local workspace provisioning failed"
                else "Failed to prepare assessment workspace. Please try again later."
            )
            raise HTTPException(status_code=500, detail=detail) from exc
    elif not _sandbox_workspace_is_ready(sandbox, task):
        # Re-materializing here would make a reconnect look successful while
        # erasing every candidate edit. Preserve the original sandbox and fail
        # closed so the candidate can retry the same session.
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="The existing workspace could not be reconnected. Please retry; no replacement workspace was created.",
        )

    started_now = was_pending
    try:
        if started_now:
            # These fields are the durable server-owned readiness record. They
            # are intentionally assigned only after workspace preparation.
            assessment.status = AssessmentStatus.IN_PROGRESS
            assessment.started_at = utcnow()
            assessment.e2b_session_id = sandbox_id
            if getattr(assessment, "credit_consumed_at", None) is None:
                # Usage-based pricing: per-Claude-call ledger debits replace
                # the legacy flat deduction. The stamp marks billing-active.
                assessment.credit_consumed_at = utcnow()
            if getattr(assessment, "cv_text_snapshot", None) is None:
                # CV evidence is assessment-scoped once work begins. Prefer
                # the role/application copy, then fall back to the candidate's
                # shared profile; later uploads for another role must not alter
                # this assessment's scoring context.
                cv_text = None
                if assessment.application_id:
                    cv_application = (
                        db.query(CandidateApplication)
                        .filter(
                            CandidateApplication.id == assessment.application_id,
                            CandidateApplication.organization_id
                            == assessment.organization_id,
                        )
                        .first()
                    )
                    if cv_application is not None and cv_application.cv_text:
                        cv_text = cv_application.cv_text
                if cv_text is None and assessment.candidate_id:
                    cv_candidate = (
                        db.query(Candidate)
                        .filter(
                            Candidate.id == assessment.candidate_id,
                            Candidate.organization_id == assessment.organization_id,
                        )
                        .first()
                    )
                    if cv_candidate is not None and cv_candidate.cv_text:
                        cv_text = cv_candidate.cv_text
                assessment.cv_text_snapshot = cv_text

        # Assessments are terminal-only. On resume this preserves the existing
        # workspace while allowing a verified runtime configuration refresh.
        assessment.ai_mode = required_ai_mode
        if started_now:
            append_assessment_timeline_event(
                assessment,
                "assessment_started",
                {"type": "started"},
            )
            # Conversational interrogation: if the task spec defines a
            # ``decision_points`` block, render the opener message and
            # persist it as ai_prompts[0] so the candidate sees Claude's
            # decision questions BEFORE typing anything. The chat
            # flattener treats an opener (empty ``message``) as an
            # assistant-only turn — Claude sees that it asked something
            # and is waiting for an answer. The chat route runs a
            # per-turn classifier against the same decision_points and
            # builds an interrogation directive into the system prompt.
            # See ``interrogation.py`` for the schema + renderer +
            # classifier; this is the entry point.
            task_for_opener = task
            opener_text = ""
            decision_points: list[dict[str, Any]] = []
            if task_for_opener is not None:
                extra = task_for_opener.extra_data if isinstance(task_for_opener.extra_data, dict) else {}
                raw_dps = extra.get("decision_points") if isinstance(extra, dict) else None
                if isinstance(raw_dps, list):
                    decision_points = [dp for dp in raw_dps if isinstance(dp, dict)]
                if decision_points:
                    opener_text = render_opener(decision_points)
            if opener_text and not (assessment.ai_prompts or []):
                # Seed every decision_point at status=unaddressed in the
                # opener record. The chat route reads this back when
                # classifying the candidate's first reply.
                seeded_state: dict[str, dict[str, str]] = {
                    str(dp.get("id")): {
                        "status": "unaddressed",
                        "raw_status": "unaddressed",
                        "rationale": "",
                    }
                    for dp in decision_points
                    if isinstance(dp.get("id"), str) and dp.get("id")
                }
                assessment.ai_prompts = [
                    {
                        "message": "",
                        "response": opener_text,
                        "opener": True,
                        "transport": "task_opener",
                        "timestamp": utcnow().isoformat(),
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "tool_calls_made": [],
                        "interrogation_state": seeded_state,
                    }
                ]
        if started_now and assessment.application_id:
            app = (
                db.query(CandidateApplication)
                .filter(
                    CandidateApplication.id == assessment.application_id,
                    CandidateApplication.organization_id == assessment.organization_id,
                )
                .first()
            )
            if app:
                ensure_pipeline_fields(app)
                initialize_pipeline_event_if_missing(
                    db,
                    app=app,
                    actor_type="system",
                    reason="Pipeline initialized at assessment start",
                )
                transition_stage(
                    db,
                    app=app,
                    to_stage="in_assessment",
                    source="system",
                    actor_type="system",
                    reason="Candidate started assessment",
                    metadata={"assessment_id": assessment.id},
                )
        db.commit()
    except Exception:
        logger.exception("Failed to commit assessment start assessment_id=%s", assessment.id)
        db.rollback()
        if created_sandbox:
            try:
                e2b.close_sandbox(sandbox)
            except Exception:
                logger.exception("Failed to close E2B sandbox after start failure assessment_id=%s", assessment.id)
        raise HTTPException(status_code=500, detail="Failed to start assessment session")

    resume_code = resume_code_for_assessment(assessment, task.starter_code or "")

    # On resume, return the candidate's *current* workspace state instead of
    # the static template — otherwise reloading the assessment shows their
    # edits as missing.
    repo_structure_for_response = task.repo_structure
    if not started_now:
        live_repo_structure = _read_sandbox_repo_files(sandbox, _workspace_repo_root(task))
        if live_repo_structure:
            repo_structure_for_response = live_repo_structure

    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=assessment.ai_prompts or [],
    )
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "sandbox_id": sandbox_id,
        "candidate_name": getattr(getattr(assessment, "candidate", None), "full_name", None),
        "organization_name": getattr(getattr(assessment, "organization", None), "name", None),
        "expires_at": getattr(assessment, "expires_at", None),
        "invite_sent_at": getattr(assessment, "invite_sent_at", None),
        "task": {
            "name": task.name,
            "description": task.description,
            "starter_code": resume_code,
            "duration_minutes": assessment.duration_minutes,
            "task_key": task.task_key,
            "role": task.role,
            "scenario": task.scenario,
            "repo_structure": repo_structure_for_response,
            "rubric_categories": candidate_rubric_view(task.evaluation_rubric),
            "evaluation_rubric": None,
            "extra_data": None,
            "proctoring_enabled": False if settings.MVP_DISABLE_PROCTORING else (task.proctoring_enabled if task else False),
            "claude_budget_limit_usd": effective_budget_limit,
        },
        "claude_budget": claude_budget,
        "time_remaining": time_remaining_seconds(assessment),
        "is_timer_paused": bool(getattr(assessment, "is_timer_paused", False)),
        "pause_reason": getattr(assessment, "pause_reason", None),
        "total_paused_seconds": int(getattr(assessment, "total_paused_seconds", 0) or 0),
        "ai_mode": getattr(assessment, "ai_mode", "claude_cli_terminal"),
        "terminal_mode": getattr(assessment, "ai_mode", "claude_cli_terminal") == "claude_cli_terminal",
        "terminal_capabilities": terminal_capabilities(),
        "repo_url": getattr(assessment, "assessment_repo_url", None),
        "branch_name": getattr(assessment, "assessment_branch", None),
        "clone_command": getattr(assessment, "clone_command", None),
        # Existing transcript (incl. task_opener Claude wrote at /start
        # time) — frontend hydrates the chat panel from this so the
        # decision questions appear immediately on first open (#37).
        "ai_prompts": list(assessment.ai_prompts or []),
        # Multi-role deliverable framing. Absent for legacy engineering
        # tasks (FE defaults to kind="code"); set when the task spec
        # declares ``deliverable`` (see task_spec_loader). The chat-first
        # FE keys off ``kind`` to (a) auto-open primary_artifact in the
        # editor and (b) reweight panes so chat is dominant.
        "deliverable": (
            (task.extra_data or {}).get("deliverable")
            if isinstance(task.extra_data, dict)
            else None
        ),
        "allow_external_clipboard": bool(
            getattr(assessment, "allow_external_clipboard", False)
        ),
    }


def _persist_post_claim_scoring_failure(
    assessment_id: int,
    db: Session,
    *,
    error: Exception,
) -> Assessment | None:
    """Persist a durable grading retry after submission has committed."""
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
                "last_error": str(error)[:1000],
            }
        )
        rubric["retry"] = retry
        breakdown["rubric_grading"] = rubric
        breakdown["scoring_failure"] = {
            "status": "retrying",
            "stage": "post_claim_submission",
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
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
                "error_type": type(error).__name__,
                "error": str(error)[:500],
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
    defer_scoring: bool = False,
    suppress_completion_side_effects: bool = False,
    enqueue_rubric_retry_on_commit: bool = True,
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
            recover_retry_sandbox_fn=None,
            retry_scoring=retry_scoring,
            defer_scoring=defer_scoring,
            suppress_completion_side_effects=suppress_completion_side_effects,
            enqueue_rubric_retry_on_commit=enqueue_rubric_retry_on_commit,
        )
    except Exception as exc:
        # Lifecycle/lease rejections and any freeze failure that left the row
        # IN_PROGRESS are pre-terminal. Never poison those resumable attempts
        # with the durable post-claim scoring-failure marker.
        db.rollback()
        current_status = (
            db.query(Assessment.status)
            .filter(Assessment.id == int(assessment.id))
            .scalar()
        )
        pre_claim_rejection = isinstance(exc, HTTPException) and exc.status_code in {
            400,
            409,
        }
        pre_claim_rejection = bool(
            pre_claim_rejection or current_status == AssessmentStatus.IN_PROGRESS
        )
        recovered = None
        if not pre_claim_rejection:
            recovered = _persist_post_claim_scoring_failure(
                int(assessment.id),
                db,
                error=exc,
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
    if defer_scoring and result.get("grading_status") == "pending":
        # The completed assessment row is already the durable outbox. This
        # best-effort kick minimizes latency; the existing minute sweep covers
        # a broker outage or worker crash without involving the candidate.
        if enqueue_rubric_retry_on_commit:
            try:
                from ...tasks.rubric_retry_tasks import retry_incomplete_rubric_scoring

                retry_incomplete_rubric_scoring.delay(int(assessment.id))
            except Exception:
                logger.exception(
                    "Failed to enqueue accepted assessment grading assessment_id=%s; sweep will recover",
                    assessment.id,
                )
        return result

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
        from ...models.role import ROLE_KIND_SISTER

        role = getattr(assessment, "role", None)
        if role is not None and str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER:
            from ...tasks.sister_role_tasks import related_role_agent_cycle

            related_role_agent_cycle.delay(int(role_id))
            return True
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
