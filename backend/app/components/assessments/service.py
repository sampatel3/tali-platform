"""Assessment business logic: start, submit, CV upload, scoring orchestration."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...platform.config import settings
from ...models.assessment import Assessment, AssessmentStatus
from ...models.organization import Organization
from ...models.candidate import Candidate
from ...models.task import Task
from ...components.integrations.e2b.service import E2BService
from ...components.integrations.claude.service import ClaudeService
from ...services.document_service import process_document_upload
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.credit_ledger_service import append_credit_ledger_entry
from ...services.task_spec_loader import candidate_rubric_view
from .claude_budget import build_claude_budget_snapshot, resolve_effective_budget_limit_usd
from .submission_runtime import submit_assessment_impl
from .terminal_runtime import resolve_ai_mode, terminal_capabilities

logger = logging.getLogger(__name__)

from .repository import (
    utcnow,
    ensure_utc,
    resume_code_for_assessment,
    append_assessment_timeline_event,
    time_remaining_seconds,
)


def _repo_files_from_structure(repo_structure: Dict[str, Any] | None) -> List[tuple[str, str]]:
    """Normalize repo_structure payload into (path, content) tuples."""
    files = (repo_structure or {}).get("files") or {}
    normalized: List[tuple[str, str]] = []

    if isinstance(files, dict):
        for path, content in files.items():
            if not path:
                continue
            if isinstance(content, str):
                normalized.append((path, content))
            else:
                normalized.append((path, json.dumps(content, indent=2, sort_keys=True)))
    elif isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if not path:
                continue
            content = entry.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, indent=2, sort_keys=True)
            normalized.append((path, content))

    return normalized


def _workspace_repo_root(task: Task) -> str:
    root_name = (task.task_key or f"assessment-{task.id}").strip() or f"assessment-{task.id}"
    safe_root = re.sub(r"[^a-zA-Z0-9._-]+", "-", root_name).strip("-") or f"assessment-{task.id}"
    return f"/workspace/{safe_root}"


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
        stdout_text = ""
        if isinstance(result, dict):
            stdout_text = str(result.get("stdout") or "")
        else:
            logs = getattr(result, "logs", None)
            raw_stdout = getattr(logs, "stdout", None) if logs is not None else None
            if isinstance(raw_stdout, list):
                stdout_text = "\n".join(str(item) for item in raw_stdout)
            elif raw_stdout is not None:
                stdout_text = str(raw_stdout)
            else:
                stdout_text = str(getattr(result, "stdout", "") or "")
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
        return ok
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

    logger.info("Materialized %d repository files under %s", len(repo_files), repo_root)


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
        out = (result.get("stdout") or "").strip().splitlines()
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
                out = (push_result.get("stdout") or "").strip().splitlines()
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
    is_demo = bool(getattr(assessment, "is_demo", False))
    # Demo assessments should remain free to start (no credit consumption).
    if was_pending and not settings.MVP_DISABLE_LEMON and not is_demo:
        org = (
            db.query(Organization)
            .filter(Organization.id == assessment.organization_id)
            .with_for_update()
            .first()
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        if getattr(assessment, "credit_consumed_at", None) is None:
            available_credits = int(org.credits_balance or 0)
            if available_credits <= 0:
                raise HTTPException(status_code=402, detail="Insufficient credits. Purchase credits to start this assessment.")
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
            raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")

    try:
        cloned = _clone_assessment_branch_into_workspace(sandbox, assessment, task)
        if not cloned:
            raise HTTPException(status_code=500, detail="Failed to clone assessment repository")
    except Exception:
        logger.exception("Failed to initialize task repository in sandbox")
        raise HTTPException(status_code=500, detail="Failed to initialize assessment repository")

    resume_code = resume_code_for_assessment(assessment, task.starter_code or "")

    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    task_extra_data = task.extra_data if isinstance(task.extra_data, dict) else {}
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
