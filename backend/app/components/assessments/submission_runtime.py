"""Assessment submission orchestration extracted from the service facade."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.notifications.service import send_results_notification_sync
from ...components.scoring.analytics import compute_all_heuristics
from ...components.scoring.service import calculate_mvp_score, generate_heuristic_summary
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.request_context import get_request_id
from ...services.candidate_feedback_engine import build_candidate_feedback_payload
from ...services.fit_matching_service import calculate_cv_job_match_sync
from .repository import (
    append_assessment_timeline_event,
    build_timeline,
    ensure_utc,
    utcnow,
)


def _terminal_usage_totals(assessment: Assessment) -> tuple[int, int]:
    """Aggregate provider usage emitted by the Claude CLI terminal transcript."""
    input_tokens = 0
    output_tokens = 0
    for entry in list(getattr(assessment, "cli_transcript", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_usage":
            continue
        input_tokens += max(0, int(entry.get("input_tokens") or 0))
        output_tokens += max(0, int(entry.get("output_tokens") or 0))
    return input_tokens, output_tokens


def _task_extra_data(task: Task) -> Dict[str, Any]:
    extra = getattr(task, "extra_data", None)
    return extra if isinstance(extra, dict) else {}


def _resolve_calibration_prompt(task: Task, settings_obj: Any) -> str:
    task_extra = _task_extra_data(task)
    return (
        (getattr(task, "calibration_prompt", None) or "").strip()
        or str(task_extra.get("calibration_prompt") or "").strip()
        or (getattr(settings_obj, "DEFAULT_CALIBRATION_PROMPT", "") or "").strip()
    )


def _resolve_calibration_subject_prompt(assessment: Assessment, prompts: List[Dict[str, Any]]) -> str:
    warmup_prompt = str(getattr(assessment, "calibration_warmup_prompt", "") or "").strip()
    if warmup_prompt:
        return warmup_prompt
    first_prompt = str((prompts[0] or {}).get("message") if prompts else "" or "").strip()
    return first_prompt


def _heuristic_calibration_score(prompt_text: str) -> float | None:
    text = str(prompt_text or "").strip()
    if not text:
        return None
    words = len([token for token in re.split(r"\s+", text) if token.strip()])
    has_question = "?" in text
    has_code_ref = bool(re.search(r"```|line\s+\d+|[A-Za-z0-9_]+\.(py|js|ts|tsx|jsx)\b|traceback|error", text, re.I))
    score = 3.5
    if words >= 15:
        score += 2.0
    if words >= 35:
        score += 1.5
    if has_question:
        score += 1.5
    if has_code_ref:
        score += 1.5
    if words < 6:
        score -= 2.0
    return round(max(0.0, min(10.0, score)), 2)


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


def _parse_test_runner_results(output: str, parse_pattern: str | None) -> Dict[str, int]:
    if not parse_pattern:
        return {"passed": 0, "failed": 0, "total": 0}

    passed = 0
    failed = 0
    total = 0

    try:
        match = re.search(parse_pattern, output or "", re.IGNORECASE | re.MULTILINE)
    except re.error:
        match = None
    if match:
        groups = match.groupdict() if hasattr(match, "groupdict") else {}
        if groups:
            try:
                passed = int(groups.get("passed") or 0)
            except (TypeError, ValueError):
                passed = 0
            try:
                failed = int(groups.get("failed") or 0)
            except (TypeError, ValueError):
                failed = 0
            try:
                total = int(groups.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
        elif match.groups():
            try:
                passed = int(match.group(1))
            except (TypeError, ValueError):
                passed = 0

    if failed == 0:
        fail_match = re.search(r"(?i)(\d+)\s+failed", output or "")
        if fail_match:
            try:
                failed = int(fail_match.group(1))
            except (TypeError, ValueError):
                failed = 0
    if total == 0:
        total = passed + failed
        if total == 0 and passed > 0:
            total = passed

    return {"passed": max(0, passed), "failed": max(0, failed), "total": max(0, total)}


def _run_task_test_runner(
    e2b: Any,
    sandbox: Any,
    task: Task,
    repo_root: str,
) -> Dict[str, Any] | None:
    config = (_task_extra_data(task).get("test_runner") or {})
    if not isinstance(config, dict):
        return None
    command = str(config.get("command") or "").strip()
    if not command:
        return None

    working_dir = str(config.get("working_dir") or repo_root).strip() or repo_root
    try:
        timeout_seconds = float(config.get("timeout_seconds") or 60)
    except (TypeError, ValueError):
        timeout_seconds = 60.0
    timeout_seconds = max(5.0, min(timeout_seconds, 600.0))
    parse_pattern = str(config.get("parse_pattern") or "").strip()

    try:
        process = e2b.run_command(
            sandbox,
            command,
            cwd=working_dir,
            timeout=timeout_seconds,
        )
        stdout, stderr, exit_code = _extract_process_output(process)
        combined = "\n".join(part for part in [stdout, stderr] if part)
        parsed = _parse_test_runner_results(combined, parse_pattern)
        passed = parsed["passed"]
        failed = parsed["failed"]
        total = parsed["total"]
        success = (failed == 0) and (exit_code in (None, 0))
        return {
            "success": success,
            "source": "task_test_runner",
            "command": command,
            "working_dir": working_dir,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "passed": passed,
            "failed": failed,
            "total": total,
        }
    except Exception as exc:
        return {
            "success": False,
            "source": "task_test_runner",
            "command": command,
            "working_dir": working_dir,
            "stdout": "",
            "stderr": "",
            "error": str(exc),
            "passed": 0,
            "failed": 0,
            "total": 0,
        }


def submit_assessment_impl(
    assessment: Assessment,
    final_code: str,
    tab_switch_count: int,
    db: Session,
    *,
    settings_obj: Any,
    e2b_service_cls: Type[Any],
    claude_service_cls: Type[Any],
    workspace_repo_root_fn: Callable[[Task], str],
    collect_git_evidence_fn: Callable[[Any, str], Dict[str, Any]],
) -> Dict[str, Any]:
    """Run tests, compute scores, persist results, and trigger notifications."""
    if assessment.status != AssessmentStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Assessment cannot be submitted in current state")

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    assessment.tab_switch_count = 0 if settings_obj.MVP_DISABLE_PROCTORING else tab_switch_count

    # Backfill last prompt's code_after
    if assessment.ai_prompts:
        prompts = list(assessment.ai_prompts)
        if prompts:
            prompts[-1] = {**prompts[-1], "code_after": final_code}
            assessment.ai_prompts = prompts

    # --- 1. Run tests ---
    repo_root = workspace_repo_root_fn(task)
    e2b = e2b_service_cls(settings_obj.E2B_API_KEY)
    if assessment.e2b_session_id:
        sandbox = e2b.connect_sandbox(assessment.e2b_session_id)
    else:
        sandbox = e2b.create_sandbox()

    sandbox.files.write("/tmp/solution.py", final_code)
    test_results = _run_task_test_runner(e2b, sandbox, task, repo_root)
    if not isinstance(test_results, dict) or (
        int(test_results.get("passed", 0) or 0) == 0
        and int(test_results.get("total", 0) or 0) == 0
        and task.test_code
    ):
        test_results = e2b.run_tests(sandbox, task.test_code)
    if not isinstance(test_results, dict):
        test_results = {"passed": 0, "failed": 0, "total": 0}

    passed = test_results.get("passed", 0)
    total = test_results.get("total", 0)

    # --- 2. Capture git evidence and persist branch state (store before push so diff not lost on failure) ---
    try:
        evidence = collect_git_evidence_fn(sandbox, repo_root)
        assessment.git_evidence = evidence
        assessment.final_repo_state = evidence.get("head_sha")
        if evidence.get("status_porcelain"):
            branch_name = (getattr(assessment, "assessment_branch", None) or "").strip()
            push_target = f"HEAD:{branch_name}" if branch_name else "HEAD"
            push_result = sandbox.run_code(
                "import json,subprocess,pathlib\n"
                f"repo=pathlib.Path({repo_root!r})\n"
                "subprocess.run(['git','add','-A'],cwd=repo,check=False,capture_output=True,text=True)\n"
                "commit=subprocess.run(['git','-c','user.email=taali@local','-c','user.name=TAALI','commit','-m','submit: candidate'],cwd=repo,check=False,capture_output=True,text=True)\n"
                f"push=subprocess.run(['git','push','origin',{push_target!r}],cwd=repo,check=False,capture_output=True,text=True)\n"
                "payload={\n"
                " 'commit_returncode': commit.returncode,\n"
                " 'commit_stderr': (commit.stderr or '')[-500:],\n"
                " 'push_returncode': push.returncode,\n"
                " 'push_stderr': (push.stderr or '')[-500:],\n"
                "}\n"
                "print(json.dumps(payload))\n"
            )
            push_payload: Dict[str, Any] = {}
            try:
                out = (push_result.get("stdout") or "").strip().splitlines()
                if out:
                    push_payload = json.loads(out[-1])
            except Exception:
                push_payload = {}

            push_rc = int(push_payload.get("push_returncode", 0) or 0)
            if push_rc != 0:
                evidence["push_returncode"] = push_rc
                evidence["push_stderr"] = push_payload.get("push_stderr", "")
                assessment.git_evidence = evidence
                if not bool(getattr(settings_obj, "GITHUB_MOCK_MODE", False)):
                    raise HTTPException(status_code=500, detail="Failed to push candidate branch updates")

            evidence = collect_git_evidence_fn(sandbox, repo_root)
            evidence["push_returncode"] = push_payload.get("push_returncode", 0)
            if push_payload.get("push_stderr"):
                evidence["push_stderr"] = push_payload.get("push_stderr", "")
            assessment.git_evidence = evidence
            assessment.final_repo_state = evidence.get("head_sha")
    except HTTPException:
        raise
    except Exception:
        import logging as _logging

        _logging.getLogger("taali.assessments").exception("Failed to capture git evidence on manual submit")
    finally:
        e2b.close_sandbox(sandbox)

    # --- 3. Prompt/session analysis + heuristics ---
    quality: Dict[str, Any] = {"success": False, "analysis": None}
    prompts = assessment.ai_prompts or []
    prompt_analysis: Dict[str, Any] = {"success": False, "scores": {}, "per_prompt_scores": [], "fraud_flags": []}
    heuristics = compute_all_heuristics(assessment, prompts)
    calibration_prompt = None
    calibration_score = None
    calibration_subject_prompt = None
    if not settings_obj.MVP_DISABLE_CALIBRATION:
        calibration_prompt = _resolve_calibration_prompt(task, settings_obj)
        calibration_subject_prompt = _resolve_calibration_subject_prompt(assessment, prompts)

    if settings_obj.MVP_DISABLE_CLAUDE_SCORING:
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
        context_utilization_score = max(
            0.0,
            min(10.0, float(code_delta.get("utilization_rate", 0) or 0) * 10.0),
        )
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
        if calibration_prompt and calibration_subject_prompt:
            calibration_score = _heuristic_calibration_score(calibration_subject_prompt)
    else:
        claude = claude_service_cls(settings_obj.ANTHROPIC_API_KEY)
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

        if calibration_prompt and calibration_subject_prompt:
            try:
                calibration = claude.analyze_prompt_session(
                    [{"message": calibration_subject_prompt}],
                    calibration_prompt,
                )
                if calibration.get("success"):
                    raw_score = calibration.get("scores", {}).get("prompt_clarity")
                    if raw_score is not None:
                        calibration_score = float(raw_score)
            except Exception:
                calibration_score = _heuristic_calibration_score(calibration_subject_prompt)

    # --- 3. CV-Job fit matching (single Claude call — done first so it feeds into scoring) ---
    scoring_errors = []
    cv_match_result = {
        "cv_job_match_score": None,
        "skills_match": None,
        "experience_relevance": None,
        "match_details": {},
    }
    try:
        candidate = (
            db.query(Candidate).filter(Candidate.id == assessment.candidate_id).first()
            if assessment.candidate_id
            else None
        )
        app_cv_text = None
        role_job_spec_text = None
        if assessment.application_id:
            app_row = db.query(CandidateApplication).filter(
                CandidateApplication.id == assessment.application_id
            ).first()
            app_cv_text = app_row.cv_text if app_row else None
        if assessment.role_id:
            role_row = db.query(Role).filter(Role.id == assessment.role_id).first()
            role_job_spec_text = role_row.job_spec_text if role_row else None

        cv_text = app_cv_text or (candidate.cv_text if candidate else None)
        job_spec_text = role_job_spec_text or (candidate.job_spec_text if candidate else None)

        if cv_text and job_spec_text and settings_obj.ANTHROPIC_API_KEY:
            cv_match_result = calculate_cv_job_match_sync(
                cv_text=cv_text,
                job_spec_text=job_spec_text,
                api_key=settings_obj.ANTHROPIC_API_KEY,
                model=settings_obj.resolved_claude_scoring_model,
            )
        elif candidate and (not cv_text or not job_spec_text):
            scoring_errors.append(
                {"component": "cv_job_match", "error": "Missing CV or job spec text — fit scoring skipped"}
            )
    except Exception as exc:
        import logging as _logging

        _logging.getLogger("taali.assessments").exception("CV-job match failed, continuing without fit score")
        scoring_errors.append({"component": "cv_job_match", "error": str(exc)})

    # --- 4. MVP composite score (30+ metrics, 8 categories) ---
    duration_seconds = 0
    if assessment.started_at:
        duration_seconds = max(0, int((utcnow() - ensure_utc(assessment.started_at)).total_seconds()))

    interactions = _build_interactions(prompts)
    task_scoring_hints = None
    task_extra_data = _task_extra_data(task)
    if isinstance(task_extra_data.get("scoring_hints"), dict):
        task_scoring_hints = task_extra_data.get("scoring_hints")

    composite = calculate_mvp_score(
        interactions=interactions,
        tests_passed=passed,
        tests_total=total,
        total_duration_seconds=duration_seconds,
        time_limit_minutes=assessment.duration_minutes or 30,
        v2_enabled=settings_obj.SCORING_V2_ENABLED,
        weights=task.score_weights if task.score_weights else None,
        cv_match_result=cv_match_result,
        task_scoring_hints=task_scoring_hints,
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
    assessment.prompt_quality_score = category_scores.get(
        "prompt_clarity",
        round((component_scores.get("clarity_score", 0) + component_scores.get("specificity_score", 0)) / 20.0, 2),
    )
    assessment.prompt_efficiency_score = category_scores.get(
        "efficiency",
        round(component_scores.get("efficiency_score", 0) / 10.0, 2),
    )
    assessment.independence_score = category_scores.get(
        "independence",
        round(component_scores.get("independence_score", 0) / 10.0, 2),
    )
    assessment.context_utilization_score = category_scores.get(
        "context_provision",
        round(component_scores.get("context_score", 0) / 10.0, 2),
    )
    assessment.design_thinking_score = round(component_scores.get("decomposition_score", 0) / 10.0, 2)
    assessment.debugging_strategy_score = round(component_scores.get("iteration_score", 0) / 10.0, 2)
    assessment.written_communication_score = category_scores.get(
        "communication",
        round(component_scores.get("clarity_score", 0) / 10.0, 2),
    )
    assessment.learning_velocity_score = round(
        composite.get("metric_details", {}).get("prompt_quality_trend", 0) * 7.0,
        2,
    )
    assessment.error_recovery_score = round(
        composite.get("metric_details", {}).get("error_recovery_score", 0) / 10.0,
        2,
    )
    assessment.requirement_comprehension_score = round(component_scores.get("specificity_score", 0) / 10.0, 2)
    assessment.calibration_score = calibration_score

    # CV-Job fit matching scores (Phase 2)
    assessment.cv_job_match_score = cv_match_result.get("cv_job_match_score")
    assessment.cv_job_match_details = cv_match_result.get("match_details", {})

    heuristic_summary = generate_heuristic_summary(
        category_scores=category_scores,
        soft_signals=composite.get("soft_signals", {}),
        fraud_flags=composite.get("fraud", {}).get("flags", []),
    )

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
        "heuristic_summary": heuristic_summary,
        "uncapped_final_score": composite.get("uncapped_final_score"),
        "applied_caps": composite.get("applied_caps", []),
        "errors": scoring_errors if scoring_errors else [],
    }
    assessment.score_weights_used = composite.get("weights_used", {})
    assessment.flags = composite.get("fraud", {}).get("flags", [])
    assessment.scored_at = utcnow()
    assessment.total_duration_seconds = duration_seconds
    assessment.total_prompts = len(interactions)
    prompt_input_tokens = sum(max(0, int(it.get("input_tokens", 0) or 0)) for it in interactions)
    prompt_output_tokens = sum(max(0, int(it.get("output_tokens", 0) or 0)) for it in interactions)
    terminal_input_tokens, terminal_output_tokens = _terminal_usage_totals(assessment)
    computed_input_tokens = prompt_input_tokens + terminal_input_tokens
    computed_output_tokens = prompt_output_tokens + terminal_output_tokens
    assessment.total_input_tokens = max(
        int(getattr(assessment, "total_input_tokens", 0) or 0),
        computed_input_tokens,
    )
    assessment.total_output_tokens = max(
        int(getattr(assessment, "total_output_tokens", 0) or 0),
        computed_output_tokens,
    )

    fraud_flags = [
        {"type": f, "confidence": 1.0, "evidence": f, "prompt_index": None}
        for f in (composite.get("fraud", {}).get("flags", []) or [])
    ]
    if (assessment.tab_switch_count or 0) > 5:
        fraud_flags.append(
            {
                "type": "tab_switching",
                "confidence": 0.8,
                "evidence": f"{assessment.tab_switch_count} tab switches recorded",
                "prompt_index": None,
            }
        )
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
        "uncapped_final_score": composite.get("uncapped_final_score"),
        "applied_caps": composite.get("applied_caps", []),
        "heuristic_summary": heuristic_summary,
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
        (
            assessment.prompt_quality_score
            + assessment.independence_score
            + assessment.prompt_efficiency_score
        )
        / 3.0,
        2,
    )
    assessment.time_efficiency_score = round(component_scores.get("time_efficiency", 0.0) / 10.0, 2)

    org = db.query(Organization).filter(Organization.id == assessment.organization_id).first()
    org_feedback_enabled = bool(getattr(org, "candidate_feedback_enabled", True)) if org else True
    assessment_feedback_enabled = bool(getattr(assessment, "candidate_feedback_enabled", True))
    if org_feedback_enabled and assessment_feedback_enabled:
        try:
            assessment.candidate_feedback_json = build_candidate_feedback_payload(
                assessment=assessment,
                db=db,
            )
            assessment.candidate_feedback_generated_at = utcnow()
            assessment.candidate_feedback_ready = True
        except Exception:
            assessment.candidate_feedback_ready = False

    try:
        db.commit()
        db.refresh(assessment)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit assessment")

    # --- 5. Notifications ---
    notify_user = db.query(User).filter(User.organization_id == assessment.organization_id).first()
    if notify_user and not settings_obj.MVP_DISABLE_CELERY:
        from ...tasks.assessment_tasks import send_results_email

        candidate_name = (
            (assessment.candidate.full_name or assessment.candidate.email)
            if assessment.candidate
            else "Candidate"
        )
        send_results_email.delay(
            user_email=notify_user.email,
            candidate_name=candidate_name,
            score=assessment.score,
            assessment_id=assessment.id,
        )

    if (
        not settings_obj.MVP_DISABLE_WORKABLE
        and not settings_obj.MVP_DISABLE_CELERY
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
                "results_url": f"{settings_obj.FRONTEND_URL}/dashboard",
            },
            request_id=get_request_id(),
        )

    if notify_user and settings_obj.MVP_DISABLE_CELERY:
        candidate_name = (
            (assessment.candidate.full_name or assessment.candidate.email)
            if assessment.candidate
            else "Candidate"
        )
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


def _build_interactions(prompts: list) -> List[Dict[str, Any]]:
    """Convert raw ai_prompts records into scoring-engine interaction dicts."""
    def _parse_ts(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    base_ts = None
    for raw in prompts:
        candidate_ts = _parse_ts((raw or {}).get("timestamp"))
        if candidate_ts is not None:
            base_ts = candidate_ts
            break

    interactions = []
    for i, p in enumerate(prompts):
        msg = p.get("message", "") or ""
        code_before = p.get("code_before")
        if not isinstance(code_before, str):
            code_before = p.get("code_context")
        code_before = code_before or ""

        code_after = p.get("code_after")
        if not isinstance(code_after, str):
            next_prompt = prompts[i + 1] if i + 1 < len(prompts) else {}
            code_after = (
                next_prompt.get("code_before")
                or next_prompt.get("code_context")
                or code_before
            )
        code_after = code_after or ""
        before_lines = code_before.splitlines()
        after_lines = code_after.splitlines()
        code_diff_lines_added = max(0, len(after_lines) - len(before_lines))
        code_diff_lines_removed = max(0, len(before_lines) - len(after_lines))

        ts = _parse_ts(p.get("timestamp"))
        time_since_assessment_start_ms = p.get("time_since_assessment_start_ms")
        if time_since_assessment_start_ms is None and ts and base_ts:
            time_since_assessment_start_ms = max(0, int((ts - base_ts).total_seconds() * 1000))
        if time_since_assessment_start_ms is None and i == 0:
            time_since_assessment_start_ms = p.get("time_since_last_prompt_ms")

        references_previous = p.get("references_previous")
        if references_previous is None:
            references_previous = bool(
                re.search(r"(?i)\b(as mentioned|previous|earlier|before|last response|you suggested)\b", msg)
            )
        retry_after_failure = p.get("retry_after_failure")
        if retry_after_failure is None:
            retry_after_failure = bool(
                re.search(r"(?i)\b(retry|try again|failed|still failing|another attempt)\b", msg)
            )

        interactions.append(
            {
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
                "code_snippet_included": p.get(
                    "code_snippet_included",
                    ("```" in msg) or bool(re.search(r"(?m)^(?: {4}|\t)\S", msg)),
                ),
                "error_message_included": p.get(
                    "error_message_included",
                    bool(re.search(r"(?i)(error|traceback|exception|failed|assert|stack trace)", msg)),
                ),
                "line_number_referenced": p.get(
                    "line_number_referenced",
                    bool(re.search(r"(?i)line\\s+\\d+|:\\d+(?::\\d+)?\\b", msg)),
                ),
                "file_reference": p.get(
                    "file_reference",
                    bool(re.search(r"(?i)(src/|app/|tests?/|\\.(py|js|jsx|ts|tsx|json|yml|yaml|md)\\b)", msg)),
                ),
                "references_previous": bool(references_previous),
                "retry_after_failure": bool(retry_after_failure),
                "time_since_assessment_start_ms": time_since_assessment_start_ms,
                "time_since_last_prompt_ms": p.get("time_since_last_prompt_ms"),
                "paste_detected": p.get("paste_detected", False),
                "paste_length": p.get("paste_length", 0) or 0,
            }
        )
    return interactions
