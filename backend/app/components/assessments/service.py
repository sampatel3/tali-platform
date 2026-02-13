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
from ...models.candidate import Candidate
from ...models.task import Task
from ...models.user import User
from ...models.organization import Organization
from ...services.e2b_service import E2BService
from ...services.claude_service import ClaudeService
from ...services.scoring_service import calculate_mvp_score
from ...services.prompt_analytics import compute_all_heuristics
from ...services.document_service import process_document_upload
from ...services.fit_matching_service import calculate_cv_job_match_sync
from ...services.assessment_repository_service import AssessmentRepositoryService
from ...services.task_spec_loader import candidate_rubric_view
from ...components.notifications.service import send_results_notification_sync

logger = logging.getLogger(__name__)

from .repository import utcnow, ensure_utc, resume_code_for_assessment, build_timeline, append_assessment_timeline_event, time_remaining_seconds


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
    clone_command = getattr(assessment, "clone_command", None)
    repo_url = getattr(assessment, "assessment_repo_url", None)
    if not clone_command or not repo_url or str(repo_url).startswith("mock://"):
        return False

    repo_root = _workspace_repo_root(task)
    result = sandbox.run_code(
        "import json, pathlib, subprocess, shlex\n"
        f"repo_root=pathlib.Path({repo_root!r})\n"
        "repo_root.parent.mkdir(parents=True, exist_ok=True)\n"
        "subprocess.run(['rm','-rf',str(repo_root)], check=False, capture_output=True)\n"
        f"cmd={clone_command!r}\n"
        "args=shlex.split(cmd)\n"
        "p=subprocess.run(args + [str(repo_root)], capture_output=True, text=True)\n"
        "payload={'returncode': p.returncode, 'stderr': p.stderr[-500:]}\n"
        "print(json.dumps(payload))\n"
    )
    try:
        lines = (result.get("stdout") or "").strip().splitlines()
        payload = json.loads(lines[-1]) if lines else {}
        return int(payload.get("returncode", 1)) == 0
    except Exception:
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
            "def run(cmd):\n"
            "  p=subprocess.run(cmd,cwd=repo,capture_output=True,text=True)\n"
            "  return p.stdout.strip()\n"
            "payload={\n"
            " 'head_sha': run(['git','rev-parse','HEAD']),\n"
            " 'status_porcelain': run(['git','status','--porcelain']),\n"
            " 'diff_main': run(['git','diff','main...HEAD']),\n"
            " 'diff_staged': run(['git','diff','--cached']),\n"
            " 'commits': run(['git','log','--oneline','--decorate','-n','50'])\n"
            "}\n"
            "print(json.dumps(payload))\n"
        )
        out = (result.get("stdout") or "").strip().splitlines()
        if out:
            return json.loads(out[-1])
    except Exception:
        logger.exception("Failed to capture git evidence from sandbox")
    return {
        "head_sha": None,
        "status_porcelain": "",
        "diff_main": "",
        "diff_staged": "",
        "commits": "",
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
            sandbox.run_code(
                "import subprocess,pathlib\n"
                f"repo=pathlib.Path({repo_root!r})\n"
                "subprocess.run(['git','add','-A'],cwd=repo,check=False,capture_output=True)\n"
                "subprocess.run(['git','-c','user.email=tali@local','-c','user.name=TALI','commit','-m','auto-submit: time expired'],cwd=repo,check=False,capture_output=True)\n"
                "subprocess.run(['git','push','origin','HEAD'],cwd=repo,check=False,capture_output=True)\n"
            )
            evidence = _collect_git_evidence_from_sandbox(sandbox, repo_root)
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

def start_or_resume_assessment(assessment: Assessment, db: Session) -> Dict[str, Any]:
    """Start a new assessment or resume an in-progress one. Returns AssessmentStart payload."""
    if assessment.status == AssessmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Assessment has already been submitted")
    if assessment.expires_at and ensure_utc(assessment.expires_at) < utcnow():
        raise HTTPException(status_code=400, detail="Assessment link has expired")
    if not (settings.E2B_API_KEY or "").strip():
        raise HTTPException(status_code=503, detail="Code environment is not configured. Please try again later.")

    sandbox = None
    sandbox_id = None
    was_pending = assessment.status == AssessmentStatus.PENDING
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
        _logging.getLogger("tali.assessments").exception("Could not start code environment")
        raise HTTPException(status_code=503, detail="Could not start code environment. Please try again later.")

    try:
        assessment.status = AssessmentStatus.IN_PROGRESS
        if was_pending or not assessment.started_at:
            assessment.started_at = utcnow()
        assessment.e2b_session_id = sandbox_id
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

    try:
        cloned = _clone_assessment_branch_into_workspace(sandbox, assessment, task)
        if not cloned:
            _materialize_task_repository(sandbox, task)
    except Exception:
        logger.exception("Failed to initialize task repository in sandbox")

    resume_code = resume_code_for_assessment(assessment, task.starter_code or "")

    task_extra_data = task.extra_data or {}
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
            "calibration_prompt": None if settings.MVP_DISABLE_CALIBRATION else (task.calibration_prompt if task else None),
            "proctoring_enabled": False if settings.MVP_DISABLE_PROCTORING else (task.proctoring_enabled if task else False),
        },
        "time_remaining": time_remaining_seconds(assessment),
        "repo_url": getattr(assessment, "assessment_repo_url", None),
        "branch_name": getattr(assessment, "assessment_branch", None),
        "clone_command": getattr(assessment, "clone_command", None),
    }


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def submit_assessment(
    assessment: Assessment,
    final_code: str,
    tab_switch_count: int,
    db: Session,
) -> Dict[str, Any]:
    """Run tests, compute scores, persist results, and trigger notifications."""
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Assessment cannot be submitted in current state")

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    assessment.tab_switch_count = 0 if settings.MVP_DISABLE_PROCTORING else tab_switch_count

    # Backfill last prompt's code_after
    if assessment.ai_prompts:
        prompts = list(assessment.ai_prompts)
        if prompts:
            prompts[-1] = {**prompts[-1], "code_after": final_code}
            assessment.ai_prompts = prompts

    # --- 1. Run tests ---
    e2b = E2BService(settings.E2B_API_KEY)
    if assessment.e2b_session_id:
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
    else:
        sandbox = e2b.create_sandbox()

    sandbox.files.write("/tmp/solution.py", final_code)
    test_results = e2b.run_tests(sandbox, task.test_code) if task.test_code else {"passed": 0, "failed": 0, "total": 0}
    if not isinstance(test_results, dict):
        test_results = {"passed": 0, "failed": 0, "total": 0, "error": "Invalid test results payload"}

    passed = test_results.get("passed", 0)
    total = test_results.get("total", 0)

    # --- 2. Capture git evidence and persist branch state (store before push so diff not lost on failure) ---
    try:
        repo_root = _workspace_repo_root(task)
        evidence = _collect_git_evidence_from_sandbox(sandbox, repo_root)
        assessment.git_evidence = evidence
        assessment.final_repo_state = evidence.get("head_sha")
        if evidence.get("status_porcelain"):
            sandbox.run_code(
                "import subprocess,pathlib\n"
                f"repo=pathlib.Path({repo_root!r})\n"
                "subprocess.run(['git','add','-A'],cwd=repo,check=False,capture_output=True)\n"
                "subprocess.run(['git','-c','user.email=tali@local','-c','user.name=TALI','commit','-m','submit: candidate'],cwd=repo,check=False,capture_output=True)\n"
                "subprocess.run(['git','push','origin','HEAD'],cwd=repo,check=False,capture_output=True)\n"
            )
            evidence = _collect_git_evidence_from_sandbox(sandbox, repo_root)
            assessment.git_evidence = evidence
            assessment.final_repo_state = evidence.get("head_sha")
    except Exception:
        logger.exception("Failed to capture git evidence on manual submit")
    finally:
        e2b.close_sandbox(sandbox)

    # --- 3. Prompt/session analysis + heuristics ---
    quality: Dict[str, Any] = {"success": False, "analysis": None}
    prompts = assessment.ai_prompts or []
    prompt_analysis: Dict[str, Any] = {"success": False, "scores": {}, "per_prompt_scores": [], "fraud_flags": []}
    heuristics = compute_all_heuristics(assessment, prompts)
    calibration_prompt = None
    calibration_score = None

    if settings.MVP_DISABLE_CLAUDE_SCORING:
        length_stats = heuristics.get("prompt_length_stats", {}) or {}
        code_delta = heuristics.get("code_delta", {}) or {}
        token_eff = heuristics.get("token_efficiency", {}) or {}
        self_corr = heuristics.get("self_correction_rate", {}) or {}
        ttfp = heuristics.get("time_to_first_prompt", {}) or {}
        copy_paste = heuristics.get("copy_paste_detection", {}) or {}

        avg_words = length_stats.get("avg_words") or 0
        prompt_quality_score = max(0.0, min(10.0, 10.0 - (abs(avg_words - 80) / 12.0)))
        prompt_efficiency_score = max(0.0, min(10.0, (token_eff.get("solve_rate", 0) * 10.0)))
        independence_score = 5.0
        if ttfp.get("value") is not None:
            first_prompt_seconds = max(0, int(ttfp.get("value") or 0))
            independence_score = max(0.0, min(10.0, min(first_prompt_seconds, 600) / 60.0))
        context_utilization_score = max(0.0, min(10.0, float(code_delta.get("utilization_rate", 0) or 0) * 10.0))
        design_thinking_score = prompt_quality_score
        debugging_strategy_score = max(0.0, min(10.0, float((self_corr.get("rate") or 0)) * 10.0))
        written_communication_score = prompt_quality_score
        learning_velocity_score = prompt_quality_score
        error_recovery_score_val = debugging_strategy_score
        requirement_comprehension_score = prompt_quality_score
        code_quality_score = 5.0
        ai_scores = {
            "prompt_clarity": round(prompt_quality_score, 2),
            "prompt_efficiency": round(prompt_efficiency_score, 2),
            "independence": round(independence_score, 2),
            "context_utilization": round(context_utilization_score, 2),
            "design_thinking": round(design_thinking_score, 2),
            "debugging_strategy": round(debugging_strategy_score, 2),
            "written_communication": round(written_communication_score, 2),
            "learning_velocity": round(learning_velocity_score, 2),
            "error_recovery": round(error_recovery_score_val, 2),
            "requirement_comprehension": round(requirement_comprehension_score, 2),
        }
        prompt_analysis["fraud_flags"] = copy_paste.get("flags", []) or []
    else:
        claude = ClaudeService(settings.ANTHROPIC_API_KEY)
        try:
            quality = claude.analyze_code_quality(final_code)
        except Exception:
            quality = {"success": False, "analysis": None}
        code_quality_score = 5.0
        if quality.get("success") and quality.get("analysis"):
            try:
                analysis = json.loads(quality["analysis"])
                cqs = analysis.get("overall_score")
                if cqs is not None:
                    code_quality_score = float(cqs)
            except (json.JSONDecodeError, TypeError):
                pass

        task_desc = task.description or task.name or ""
        if prompts:
            try:
                prompt_analysis = claude.analyze_prompt_session(prompts, task_desc)
            except Exception:
                pass
        ai_scores = prompt_analysis.get("scores", {})

        if not settings.MVP_DISABLE_CALIBRATION:
            calibration_prompt = (task.calibration_prompt or settings.DEFAULT_CALIBRATION_PROMPT or "").strip()
            if calibration_prompt and prompts:
                try:
                    calibration = claude.analyze_prompt_session([prompts[0]], calibration_prompt)
                    if calibration.get("success"):
                        raw_score = calibration.get("scores", {}).get("prompt_clarity")
                        if raw_score is not None:
                            calibration_score = float(raw_score)
                except Exception:
                    calibration_score = None

    # --- 3. CV-Job fit matching (single Claude call — done first so it feeds into scoring) ---
    scoring_errors = []
    cv_match_result = {"cv_job_match_score": None, "skills_match": None, "experience_relevance": None, "match_details": {}}
    try:
        candidate = db.query(Candidate).filter(Candidate.id == assessment.candidate_id).first() if assessment.candidate_id else None
        if candidate and candidate.cv_text and candidate.job_spec_text and settings.ANTHROPIC_API_KEY:
            cv_match_result = calculate_cv_job_match_sync(
                cv_text=candidate.cv_text,
                job_spec_text=candidate.job_spec_text,
                api_key=settings.ANTHROPIC_API_KEY,
            )
        elif candidate and (not candidate.cv_text or not candidate.job_spec_text):
            scoring_errors.append({"component": "cv_job_match", "error": "Missing CV or job spec text — fit scoring skipped"})
    except Exception as exc:
        import logging as _logging
        _logging.getLogger("tali.assessments").exception("CV-job match failed, continuing without fit score")
        scoring_errors.append({"component": "cv_job_match", "error": str(exc)})

    # --- 4. MVP composite score (30+ metrics, 8 categories) ---
    duration_seconds = 0
    if assessment.started_at:
        duration_seconds = max(0, int((utcnow() - ensure_utc(assessment.started_at)).total_seconds()))

    interactions = _build_interactions(prompts)

    composite = calculate_mvp_score(
        interactions=interactions,
        tests_passed=passed,
        tests_total=total,
        total_duration_seconds=duration_seconds,
        time_limit_minutes=assessment.duration_minutes or 30,
        v2_enabled=settings.SCORING_V2_ENABLED,
        weights=task.score_weights if task.score_weights else None,
        cv_match_result=cv_match_result,
    )
    final_score_100 = composite["final_score"]
    final_score = round(final_score_100 / 10.0, 1)
    component_scores = composite["component_scores"]
    category_scores = composite.get("category_scores", {})
    per_prompt_scores = composite.get("per_prompt_scores", [])
    detailed_scores = composite.get("detailed_scores", {})
    explanations = composite.get("explanations", {})

    # --- 4. Persist ---
    assessment.status = AssessmentStatus.COMPLETED
    assessment.completed_due_to_timeout = False
    assessment.completed_at = datetime.now(timezone.utc)
    assessment.score = final_score
    assessment.final_score = final_score_100
    assessment.tests_passed = passed
    assessment.tests_total = total
    assessment.tests_run_count = total
    assessment.tests_pass_count = passed
    assessment.test_results = test_results
    assessment.code_snapshots = [
        {"prompt_index": i, "code_before": p.get("code_before", ""), "code_after": p.get("code_after", "")}
        for i, p in enumerate(prompts)
    ] + [{"final": final_code}]

    append_assessment_timeline_event(
        assessment,
        "assessment_submit",
        {
            "session_id": assessment.e2b_session_id,
            "final_code_length": len(final_code or ""),
            "tests_passed": passed,
            "tests_total": total,
            "duration_seconds": duration_seconds,
            "tab_switch_count": assessment.tab_switch_count,
        },
    )
    existing_timeline = list(assessment.timeline or [])
    derived_timeline = build_timeline(assessment)
    assessment.timeline = existing_timeline + [e for e in derived_timeline if e not in existing_timeline]
    assessment.code_quality_score = code_quality_score

    # Map category scores (0-10) to individual assessment columns for the radar chart.
    # These columns are read directly by the frontend radar chart.
    assessment.prompt_quality_score = category_scores.get("prompt_clarity", round((component_scores.get("clarity_score", 0) + component_scores.get("specificity_score", 0)) / 20.0, 2))
    assessment.prompt_efficiency_score = category_scores.get("efficiency", round(component_scores.get("efficiency_score", 0) / 10.0, 2))
    assessment.independence_score = category_scores.get("independence", round(component_scores.get("independence_score", 0) / 10.0, 2))
    assessment.context_utilization_score = category_scores.get("context_provision", round(component_scores.get("context_score", 0) / 10.0, 2))
    assessment.design_thinking_score = round(component_scores.get("decomposition_score", 0) / 10.0, 2)
    assessment.debugging_strategy_score = round(component_scores.get("iteration_score", 0) / 10.0, 2)
    assessment.written_communication_score = category_scores.get("communication", round(component_scores.get("clarity_score", 0) / 10.0, 2))
    assessment.learning_velocity_score = round(composite.get("metric_details", {}).get("prompt_quality_trend", 0) * 7.0, 2)
    assessment.error_recovery_score = round(composite.get("metric_details", {}).get("error_recovery_score", 0) / 10.0, 2)
    assessment.requirement_comprehension_score = round(component_scores.get("specificity_score", 0) / 10.0, 2)
    assessment.calibration_score = calibration_score

    # CV-Job fit matching scores (Phase 2)
    assessment.cv_job_match_score = cv_match_result.get("cv_job_match_score")
    assessment.cv_job_match_details = cv_match_result.get("match_details", {})

    # Store the full breakdown: component scores (0-100) + 8 category scores (0-10) +
    # detailed per-metric scores + explanations + fit match
    assessment.score_breakdown = {
        **component_scores,
        "category_scores": category_scores,
        "detailed_scores": detailed_scores,
        "explanations": explanations,
        "cv_job_match": {
            "overall": cv_match_result.get("cv_job_match_score"),
            "skills": cv_match_result.get("skills_match"),
            "experience": cv_match_result.get("experience_relevance"),
        },
        "errors": scoring_errors if scoring_errors else [],
    }
    assessment.score_weights_used = composite.get("weights_used", {})
    assessment.flags = composite.get("fraud", {}).get("flags", [])
    assessment.scored_at = utcnow()
    assessment.total_duration_seconds = duration_seconds
    assessment.total_prompts = len(interactions)
    assessment.total_input_tokens = sum(it.get("input_tokens", 0) for it in interactions)
    assessment.total_output_tokens = sum(it.get("output_tokens", 0) for it in interactions)

    fraud_flags = [
        {"type": f, "confidence": 1.0, "evidence": f, "prompt_index": None}
        for f in (composite.get("fraud", {}).get("flags", []) or [])
    ]
    if (assessment.tab_switch_count or 0) > 5:
        fraud_flags.append({
            "type": "tab_switching",
            "confidence": 0.8,
            "evidence": f"{assessment.tab_switch_count} tab switches recorded",
            "prompt_index": None,
        })
    assessment.prompt_fraud_flags = fraud_flags

    # Build prompt_analytics with all the data the frontend needs.
    # The frontend reads: ai_scores (for radar fallback), per_prompt_scores (line chart),
    # component_scores (bar chart), weights_used (bar chart labels).
    assessment.prompt_analytics = {
        "ai_scores": {
            "prompt_clarity": assessment.prompt_quality_score,
            "prompt_efficiency": assessment.prompt_efficiency_score,
            "independence": assessment.independence_score,
            "context_utilization": assessment.context_utilization_score,
            "design_thinking": assessment.design_thinking_score,
            "debugging_strategy": assessment.debugging_strategy_score,
            "written_communication": assessment.written_communication_score,
            "learning_velocity": assessment.learning_velocity_score,
            "error_recovery": assessment.error_recovery_score,
            "requirement_comprehension": assessment.requirement_comprehension_score,
            "prompt_specificity": round(component_scores.get("specificity_score", 0) / 10.0, 2),
            "prompt_progression": assessment.learning_velocity_score,
        },
        "per_prompt_scores": per_prompt_scores,
        "component_scores": {k: round(v / 10.0, 2) for k, v in component_scores.items()},
        "weights_used": composite.get("weights_used", {}),
        "category_scores": category_scores,
        "heuristics": heuristics,
        "metric_details": composite.get("metric_details", {}),
        "soft_signals": composite.get("soft_signals", {}),
        "fraud": composite.get("fraud", {}),
        "final_score": final_score_100,
        "flags": composite.get("fraud", {}).get("flags", []),
        "calibration_prompt": calibration_prompt,
        "calibration_score": calibration_score,
        "v2": composite.get("v2", {}),
        "cv_job_match": {
            "overall": cv_match_result.get("cv_job_match_score"),
            "skills": cv_match_result.get("skills_match"),
            "experience": cv_match_result.get("experience_relevance"),
            "details": cv_match_result.get("match_details", {}),
        },
        "detailed_scores": detailed_scores,
        "explanations": explanations,
    }

    focus = heuristics.get("browser_focus_ratio", {})
    assessment.browser_focus_ratio = focus.get("ratio")
    if assessment.time_to_first_prompt_seconds is None:
        assessment.time_to_first_prompt_seconds = (heuristics.get("time_to_first_prompt", {}) or {}).get("value")
    assessment.ai_usage_score = round(
        (assessment.prompt_quality_score + assessment.independence_score + assessment.prompt_efficiency_score) / 3.0,
        2,
    )
    assessment.time_efficiency_score = round(component_scores.get("time_efficiency", 0.0) / 10.0, 2)

    try:
        db.commit()
        db.refresh(assessment)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit assessment")

    # --- 5. Notifications ---
    notify_user = db.query(User).filter(User.organization_id == assessment.organization_id).first()
    if notify_user and not settings.MVP_DISABLE_CELERY:
        from ...tasks.assessment_tasks import send_results_email
        candidate_name = (assessment.candidate.full_name or assessment.candidate.email) if assessment.candidate else "Candidate"
        send_results_email.delay(
            user_email=notify_user.email,
            candidate_name=candidate_name,
            score=assessment.score,
            assessment_id=assessment.id,
        )

    org = db.query(Organization).filter(Organization.id == assessment.organization_id).first()
    if (
        not settings.MVP_DISABLE_WORKABLE
        and not settings.MVP_DISABLE_CELERY
        and org
        and org.workable_connected
        and org.workable_access_token
        and org.workable_subdomain
        and assessment.workable_candidate_id
    ):
        from ...tasks.assessment_tasks import post_results_to_workable
        post_results_to_workable.delay(
            access_token=org.workable_access_token,
            subdomain=org.workable_subdomain,
            candidate_id=assessment.workable_candidate_id,
            assessment_data={
                "score": assessment.score or 0,
                "tests_passed": assessment.tests_passed or 0,
                "tests_total": assessment.tests_total or 0,
                "time_taken": assessment.duration_minutes,
                "results_url": f"{settings.FRONTEND_URL}/#/dashboard",
            },
            request_id=get_request_id(),
        )

    if notify_user and settings.MVP_DISABLE_CELERY:
        candidate_name = (assessment.candidate.full_name or assessment.candidate.email) if assessment.candidate else "Candidate"
        send_results_notification_sync(
            user_email=notify_user.email,
            candidate_name=candidate_name,
            score=assessment.score or 0,
            assessment_id=assessment.id,
        )

    return {
        "success": True,
        "score": assessment.score,
        "tests_passed": passed,
        "tests_total": total,
        "quality_analysis": quality.get("analysis") if quality.get("success") else None,
        "prompt_scores": ai_scores,
        "component_scores": component_scores,
        "fraud_flags": composite.get("fraud", {}).get("flags", []),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_interactions(prompts: list) -> List[Dict[str, Any]]:
    """Convert raw ai_prompts records into scoring-engine interaction dicts."""
    interactions = []
    for i, p in enumerate(prompts):
        msg = p.get("message", "") or ""
        code_before = p.get("code_before", "") or ""
        code_after = p.get("code_after", "") or ""
        before_lines = code_before.splitlines()
        after_lines = code_after.splitlines()
        code_diff_lines_added = max(0, len(after_lines) - len(before_lines))
        code_diff_lines_removed = max(0, len(before_lines) - len(after_lines))
        interactions.append({
            "id": str(p.get("id") or i + 1),
            "sequence_number": i + 1,
            "timestamp": p.get("timestamp"),
            "message": msg,
            "response": p.get("response", "") or "",
            "input_tokens": p.get("input_tokens", 0) or 0,
            "output_tokens": p.get("output_tokens", 0) or 0,
            "response_latency_ms": p.get("response_latency_ms"),
            "code_before": code_before,
            "code_after": code_after,
            "code_diff_lines_added": code_diff_lines_added,
            "code_diff_lines_removed": code_diff_lines_removed,
            "word_count": p.get("word_count") or len(msg.split()),
            "question_count": p.get("question_count") or msg.count("?"),
            "code_snippet_included": p.get("code_snippet_included", "```" in msg),
            "error_message_included": p.get("error_message_included", bool(re.search(r"(?i)(error|traceback|exception)", msg))),
            "line_number_referenced": p.get("line_number_referenced", bool(re.search(r"(?i)line\\s+\\d+", msg))),
            "file_reference": p.get("file_reference", bool(re.search(r"(?i)\\.(py|js|jsx|ts|tsx|json|yml|yaml|md)\\b", msg))),
            "time_since_assessment_start_ms": p.get("time_since_assessment_start_ms") or (
                p.get("time_since_last_prompt_ms") if i == 0 else None
            ),
            "time_since_last_prompt_ms": p.get("time_since_last_prompt_ms"),
            "paste_detected": p.get("paste_detected", False),
            "paste_length": p.get("paste_length", 0) or 0,
        })
    return interactions
