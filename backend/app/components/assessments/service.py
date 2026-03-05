"""Assessment business logic: start, submit, CV upload, scoring orchestration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
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
from ...components.integrations.claude.service import ClaudeService
from ...services.document_service import process_document_upload
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.credit_ledger_service import append_credit_ledger_entry
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root
from ...services.task_repo_service import normalize_repo_files
from ...services.task_spec_loader import candidate_rubric_view
from ...domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from .claude_budget import build_claude_budget_snapshot, resolve_effective_budget_limit_usd
from .submission_runtime import submit_assessment_impl
from .terminal_runtime import resolve_ai_mode, terminal_capabilities

logger = logging.getLogger(__name__)

INSUFFICIENT_CREDITS_DETAIL = "Insufficient credits. Purchase credits to start this assessment."
CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE = (
    "This assessment is not available yet. Please contact the hiring team to continue."
)
ORG_INSUFFICIENT_CREDITS_MESSAGE = (
    "No assessment credits available. Purchase credits before creating a new assessment."
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


def _auto_submit_on_timeout(assessment: Assessment, task: Task, db: Session) -> None:
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        return
    code = resume_code_for_assessment(assessment, task.starter_code or "")
    try:
        e2b = E2BService(settings.E2B_API_KEY)
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id) if assessment.e2b_session_id else e2b.create_sandbox()
        repo_root = _workspace_repo_root(task)
        evidence = _collect_git_evidence_from_sandbox(sandbox, repo_root)
        # Persist evidence before push so we never lose diff if push fails (G1.4)
        assessment.git_evidence = evidence
        assessment.final_repo_state = evidence.get("head_sha")
        if evidence.get("status_porcelain"):
            branch_name = (getattr(assessment, "assessment_branch", None) or "").strip()
            push_target = f"HEAD:{branch_name}" if branch_name else "HEAD"
            push_result = sandbox.run_code(
                "import json,subprocess,pathlib\n"
                f"repo=pathlib.Path({repo_root!r})\n"
                "subprocess.run(['git','add','-A'],cwd=repo,check=False,capture_output=True,text=True)\n"
                "commit=subprocess.run(['git','-c','user.email=taali@local','-c','user.name=TAALI','commit','-m','auto-submit: time expired'],cwd=repo,check=False,capture_output=True,text=True)\n"
                f"push=subprocess.run(['git','push','origin',{push_target!r}],cwd=repo,check=False,capture_output=True,text=True)\n"
                "print(json.dumps({'commit_returncode': commit.returncode, 'push_returncode': push.returncode, 'push_stderr': (push.stderr or '')[-500:]}))\n"
            )
            push_payload: Dict[str, Any] = {}
            try:
                out = _execution_stdout_text(push_result).strip().splitlines()
                if out:
                    push_payload = json.loads(out[-1])
            except Exception:
                push_payload = {}
            evidence = _collect_git_evidence_from_sandbox(sandbox, repo_root)
            evidence["push_returncode"] = push_payload.get("push_returncode")
            if push_payload.get("push_stderr"):
                evidence["push_stderr"] = push_payload.get("push_stderr")
            assessment.git_evidence = evidence
            assessment.final_repo_state = evidence.get("head_sha")
    except Exception:
        logger.exception("Timeout finalization failed to collect git evidence")
        assessment.git_evidence = assessment.git_evidence or {"error": "git_evidence_capture_failed"}

    assessment.completed_due_to_timeout = True
    assessment.status = AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assessment.completed_at = utcnow()
    prompts = list(assessment.ai_prompts or [])
    if prompts:
        prompts[-1] = {**prompts[-1], "code_after": code}
        assessment.ai_prompts = prompts
    append_assessment_timeline_event(assessment, "auto_submit_timeout", {"final_repo_state": assessment.final_repo_state})
    if assessment.application_id:
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
                reason="Pipeline initialized at timeout completion",
            )
            transition_stage(
                db,
                app=app,
                to_stage="review",
                source="system",
                actor_type="system",
                reason="Assessment auto-completed on timeout",
                metadata={"assessment_id": assessment.id, "completed_due_to_timeout": True},
            )
    db.commit()


def enforce_active_or_timeout(assessment: Assessment, db: Session) -> None:
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        return
    if time_remaining_seconds(assessment) > 0:
        return
    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _auto_submit_on_timeout(assessment, task, db)
    raise HTTPException(status_code=409, detail="Assessment time expired and was auto-submitted")


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
    if settings.MVP_DISABLE_LEMON:
        return {
            "can_create": True,
            "reason": None,
            "message": None,
            "organization": org,
            "credits_balance": credits_balance,
            "reserved_pending_assessments": 0,
            "remaining_capacity": credits_balance,
        }

    now = utcnow()
    reserved_query = db.query(Assessment).filter(
        Assessment.organization_id == organization_id,
        Assessment.is_voided.is_(False),
        Assessment.is_demo.is_(False),
        Assessment.credit_consumed_at.is_(None),
        Assessment.status == AssessmentStatus.PENDING,
        (Assessment.expires_at.is_(None)) | (Assessment.expires_at >= now),
    )
    if exclude_assessment_id is not None:
        reserved_query = reserved_query.filter(Assessment.id != exclude_assessment_id)
    reserved_pending_assessments = reserved_query.count()
    remaining_capacity = credits_balance - reserved_pending_assessments
    if remaining_capacity <= 0:
        reason = "insufficient_credits" if credits_balance <= 0 else "credits_reserved"
        message = (
            ORG_INSUFFICIENT_CREDITS_MESSAGE
            if reason == "insufficient_credits"
            else ORG_RESERVED_CREDITS_MESSAGE
        )
        return {
            "can_create": False,
            "reason": reason,
            "message": message,
            "organization": org,
            "credits_balance": credits_balance,
            "reserved_pending_assessments": reserved_pending_assessments,
            "remaining_capacity": remaining_capacity,
        }

    return {
        "can_create": True,
        "reason": None,
        "message": None,
        "organization": org,
        "credits_balance": credits_balance,
        "reserved_pending_assessments": reserved_pending_assessments,
        "remaining_capacity": remaining_capacity,
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
    if not was_pending or settings.MVP_DISABLE_LEMON or is_demo:
        return {"can_start": True, "reason": None, "message": None, "organization": None}

    org_query = db.query(Organization).filter(Organization.id == assessment.organization_id)
    if lock_organization:
        org_query = org_query.with_for_update()
    org = org_query.first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if getattr(assessment, "credit_consumed_at", None) is not None:
        return {"can_start": True, "reason": None, "message": None, "organization": org}

    if int(org.credits_balance or 0) <= 0:
        return {
            "can_start": False,
            "reason": "insufficient_credits",
            "message": CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE,
            "organization": org,
        }

    return {"can_start": True, "reason": None, "message": None, "organization": org}


def start_or_resume_assessment(
    assessment: Assessment,
    db: Session,
    calibration_warmup_prompt: str | None = None,
) -> Dict[str, Any]:
    """Start a new assessment or resume an in-progress one. Returns AssessmentStart payload."""
    if assessment.status == AssessmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Assessment has already been submitted")
    if assessment.expires_at and ensure_utc(assessment.expires_at) < utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")
    if not (settings.E2B_API_KEY or "").strip():
        raise HTTPException(status_code=503, detail="Code environment is not configured. Please try again later.")
    try:
        required_ai_mode = resolve_ai_mode()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Assessment AI runtime is not configured: {exc}") from exc

    sandbox = None
    sandbox_id = None
    was_pending = assessment.status == AssessmentStatus.PENDING
    start_gate = get_assessment_start_gate(assessment, db, lock_organization=True)
    org = start_gate.get("organization")
    if not start_gate.get("can_start"):
        raise HTTPException(status_code=402, detail=INSUFFICIENT_CREDITS_DETAIL)
    if was_pending and org is not None and getattr(assessment, "credit_consumed_at", None) is None:
        append_credit_ledger_entry(
            db,
            organization=org,
            delta=-1,
            reason="assessment_started",
            external_ref=f"assessment_start:{assessment.id}",
            assessment_id=assessment.id,
            metadata={"assessment_id": assessment.id, "reason": "assessment_started"},
        )
        assessment.credit_consumed_at = utcnow()
    try:
        e2b = E2BService(settings.E2B_API_KEY)
        if assessment.status == AssessmentStatus.IN_PROGRESS and assessment.e2b_session_id:
            try:
                sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
            except Exception:
                sandbox = e2b.create_sandbox()
        else:
            sandbox = e2b.create_sandbox()
        sandbox_id = e2b.get_sandbox_id(sandbox)
    except Exception as e:
        import logging as _logging
        _logging.getLogger("taali.assessments").exception("Could not start code environment")
        raise HTTPException(status_code=503, detail="Could not start code environment. Please try again later.")

    started_now = False
    try:
        assessment.status = AssessmentStatus.IN_PROGRESS
        if was_pending or not assessment.started_at:
            assessment.started_at = utcnow()
            started_now = True
        warmup_text = (calibration_warmup_prompt or "").strip()
        if warmup_text and (started_now or not getattr(assessment, "calibration_warmup_prompt", None)):
            assessment.calibration_warmup_prompt = warmup_text[:4000]
        # Assessments are terminal-only.
        assessment.ai_mode = required_ai_mode
        assessment.e2b_session_id = sandbox_id
        if started_now:
            append_assessment_timeline_event(
                assessment,
                "assessment_started",
                {"type": "started"},
            )
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
        db.rollback()
        try:
            e2b.close_sandbox(sandbox)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to start assessment session")
    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    repo_service = AssessmentRepositoryService(settings.GITHUB_ORG, settings.GITHUB_TOKEN)
    if not getattr(assessment, "assessment_branch", None):
        try:
            repo_service.create_template_repo(task)
            branch_ctx = repo_service.create_assessment_branch(task, assessment.id)
            assessment.assessment_repo_url = branch_ctx.repo_url
            assessment.assessment_branch = branch_ctx.branch_name
            assessment.clone_command = branch_ctx.clone_command
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to create assessment repository branch")
            if not _is_demo_workspace_fallback_enabled(assessment):
                raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")
            logger.warning(
                "Falling back to local task repo materialization for demo assessment=%s after branch creation failure",
                assessment.id,
            )

    try:
        cloned = _clone_assessment_branch_into_workspace(sandbox, assessment, task)
        if not cloned and _is_demo_workspace_fallback_enabled(assessment):
            _materialize_task_repository(sandbox, task)
        elif not cloned:
            raise HTTPException(status_code=500, detail="Failed to clone assessment repository")
    except Exception:
        logger.exception("Failed to initialize task repository in sandbox")
        if not _is_demo_workspace_fallback_enabled(assessment):
            raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")
        try:
            _materialize_task_repository(sandbox, task)
        except Exception:
            logger.exception("Failed to materialize demo task repository in sandbox")
            raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")

    bootstrap_result = _run_workspace_bootstrap(e2b, sandbox, task, _workspace_repo_root(task))
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
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Failed to persist assessment workspace bootstrap logs")
        if not bootstrap_result.get("success") and bootstrap_result.get("must_succeed"):
            raise HTTPException(
                status_code=500,
                detail="Failed to prepare assessment workspace. Please try again later.",
            )

    resume_code = resume_code_for_assessment(assessment, task.starter_code or "")

    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    task_extra_data = _task_extra_data(task)
    task_calibration_prompt = (
        (task.calibration_prompt or "").strip()
        or str(task_extra_data.get("calibration_prompt") or "").strip()
    )
    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=assessment.ai_prompts or [],
    )
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "sandbox_id": sandbox_id,
        "task": {
            "name": task.name,
            "description": task.description,
            "starter_code": resume_code,
            "duration_minutes": assessment.duration_minutes,
            "task_key": task.task_key,
            "role": task.role,
            "scenario": task.scenario,
            "repo_structure": task.repo_structure,
            "rubric_categories": candidate_rubric_view(task.evaluation_rubric),
            "evaluation_rubric": None,
            "extra_data": None,
            "calibration_prompt": None if settings.MVP_DISABLE_CALIBRATION else (task_calibration_prompt or None),
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
    }


def submit_assessment(
    assessment: Assessment,
    final_code: str,
    tab_switch_count: int,
    db: Session,
) -> Dict[str, Any]:
    return submit_assessment_impl(
        assessment,
        final_code,
        tab_switch_count,
        db,
        settings_obj=settings,
        e2b_service_cls=E2BService,
        claude_service_cls=ClaudeService,
        workspace_repo_root_fn=_workspace_repo_root,
        collect_git_evidence_fn=_collect_git_evidence_from_sandbox,
    )
