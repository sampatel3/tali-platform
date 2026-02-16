"""Assessment DB helpers, serialization, and query utilities."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...services.evaluation_result_service import normalize_stored_evaluation_result


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return datetime as timezone-aware UTC for subtraction."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def time_remaining_seconds(assessment: Assessment) -> int:
    total = (assessment.duration_minutes or 30) * 60
    if not assessment.started_at:
        return total
    paused_seconds = int(getattr(assessment, "total_paused_seconds", 0) or 0)
    if getattr(assessment, "is_timer_paused", False) and getattr(assessment, "paused_at", None):
        paused_at = ensure_utc(assessment.paused_at)
        if paused_at:
            paused_seconds += max(0, int((utcnow() - paused_at).total_seconds()))
    elapsed = int((utcnow() - ensure_utc(assessment.started_at)).total_seconds()) - paused_seconds
    return max(0, total - max(0, elapsed))


def resume_code_for_assessment(assessment: Assessment, fallback_code: str) -> str:
    """Return best-effort code snapshot for resume flow."""
    snapshots = assessment.code_snapshots or []
    if isinstance(snapshots, list):
        for item in reversed(snapshots):
            if isinstance(item, dict):
                if isinstance(item.get("final"), str):
                    return item["final"]
                if isinstance(item.get("code_after"), str):
                    return item["code_after"]
                if isinstance(item.get("code_before"), str):
                    return item["code_before"]
    prompts = assessment.ai_prompts or []
    if isinstance(prompts, list):
        for prompt in reversed(prompts):
            if isinstance(prompt, dict):
                if isinstance(prompt.get("code_after"), str):
                    return prompt["code_after"]
                if isinstance(prompt.get("code_before"), str):
                    return prompt["code_before"]
                if isinstance(prompt.get("code_context"), str):
                    return prompt["code_context"]
    return fallback_code


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_active_assessment(assessment_id: int, db: Session) -> Assessment:
    """Get an in-progress assessment, raising 404 if not found or not active."""
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.status == AssessmentStatus.IN_PROGRESS,
    ).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Active assessment not found")
    return assessment


def validate_assessment_token(assessment: Assessment, token: str) -> None:
    """Verify the provided token matches the assessment's token."""
    if not secrets.compare_digest(assessment.token, token):
        raise HTTPException(status_code=403, detail="Invalid assessment token")


def append_assessment_timeline_event(
    assessment: Assessment,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a structured telemetry event to assessment.timeline."""
    timeline = list(assessment.timeline or [])
    timeline.append(
        {
            "event_type": event_type,
            "timestamp": utcnow().isoformat(),
            **(payload or {}),
        }
    )
    assessment.timeline = timeline


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def build_timeline(assessment: Assessment) -> List[Dict[str, Any]]:
    """Build timeline events for candidate detail (start, optional AI prompts, submit)."""
    events = list(assessment.timeline or [])
    if assessment.started_at:
        events.append({"time": "00:00", "event": "Started assessment"})
    prompts = assessment.ai_prompts or []
    start_utc = ensure_utc(assessment.started_at) if assessment.started_at else None
    for p in prompts:
        ts = p.get("timestamp") or ""
        if ts and start_utc:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta_sec = int((dt - start_utc).total_seconds())
                mm, ss = delta_sec // 60, delta_sec % 60
                time_str = f"{mm:02d}:{ss:02d}"
            except Exception:
                time_str = "\u2014"
        else:
            time_str = "\u2014"
        events.append({
            "time": time_str,
            "event": "Used AI assistant",
            "prompt": p.get("message", ""),
        })
    if assessment.completed_at and assessment.started_at:
        end_utc = ensure_utc(assessment.completed_at)
        if start_utc:
            delta_sec = int((end_utc - start_utc).total_seconds())
            mm, ss = delta_sec // 60, delta_sec % 60
            time_str = f"{mm:02d}:{ss:02d}"
        else:
            time_str = "\u2014"
        events.append({"time": time_str, "event": "Submitted assessment"})
    elif assessment.completed_at:
        events.append({"time": "\u2014", "event": "Submitted assessment"})
    return events


def build_prompts_list(assessment: Assessment) -> List[Dict[str, Any]]:
    """Build prompts_list for candidate detail from ai_prompts."""
    prompts = assessment.ai_prompts or []
    return [
        {
            "text": p.get("message", ""),
            "message": p.get("message", ""),
            "response": p.get("response", ""),
            "assessment": p.get("assessment", ""),
            "paste_detected": p.get("paste_detected", False),
            "response_latency_ms": p.get("response_latency_ms"),
            "timestamp": p.get("timestamp"),
        }
        for p in prompts
    ]


def build_results(assessment: Assessment) -> List[Dict[str, Any]]:
    """Build results list for candidate detail from test_results and code quality."""
    results = []
    if assessment.tests_total is not None and assessment.tests_total > 0:
        results.append({
            "title": "Test suite",
            "score": f"{assessment.tests_passed or 0}/{assessment.tests_total}",
            "description": f"Passed {assessment.tests_passed or 0} of {assessment.tests_total} tests.",
        })
    if assessment.test_results and isinstance(assessment.test_results, dict):
        err = assessment.test_results.get("error")
        if err:
            results.append({
                "title": "Execution",
                "score": "\u2014",
                "description": err,
            })
    if assessment.code_quality_score is not None:
        results.append({
            "title": "Code quality",
            "score": f"{assessment.code_quality_score}/10",
            "description": "Claude code quality analysis applied.",
        })
    return results


def build_breakdown(assessment: Assessment) -> Dict[str, Any]:
    """Build breakdown for candidate detail summary card.

    Returns camelCase keys matching the frontend's expected format.
    Includes both the summary metrics (for the header card) and category
    scores (for the radar chart and expandable sections).
    """
    breakdown: Dict[str, Any] = {}

    # Summary card metrics
    if assessment.tests_passed is not None and assessment.tests_total is not None:
        breakdown["testsPassed"] = f"{assessment.tests_passed}/{assessment.tests_total}"
    if assessment.code_quality_score is not None:
        breakdown["codeQuality"] = round(assessment.code_quality_score, 1)
    if assessment.ai_usage_score is not None:
        breakdown["aiUsage"] = round(assessment.ai_usage_score, 1)
    elif assessment.ai_prompts:
        n = len(assessment.ai_prompts)
        breakdown["aiUsage"] = 8 if n <= 5 else (7 if n <= 10 else 6)
    if assessment.time_efficiency_score is not None:
        breakdown["timeEfficiency"] = round(assessment.time_efficiency_score, 1)
    breakdown["bugsFixed"] = breakdown.get("testsPassed", "\u2014")

    # Category scores (0-10) from score_breakdown if available
    sb = assessment.score_breakdown
    if isinstance(sb, dict) and "category_scores" in sb:
        cats = sb["category_scores"]
        breakdown["categoryScores"] = cats
        # Also provide as a flat convenience mapping
        breakdown["communication"] = cats.get("communication")
        breakdown["independence"] = cats.get("independence")
        breakdown["promptClarity"] = cats.get("prompt_clarity")
        breakdown["contextProvision"] = cats.get("context_provision")
        breakdown["taskCompletion"] = cats.get("task_completion")
        breakdown["utilization"] = cats.get("utilization")
        breakdown["approach"] = cats.get("approach")
        breakdown["efficiency"] = cats.get("efficiency")
        breakdown["cvMatch"] = cats.get("cv_match")

    # Detailed per-metric scores (30+ metrics across 8 categories)
    if isinstance(sb, dict) and "detailed_scores" in sb:
        breakdown["detailedScores"] = sb["detailed_scores"]
    if isinstance(sb, dict) and "explanations" in sb:
        breakdown["explanations"] = sb["explanations"]

    # CV-Job match details
    if isinstance(sb, dict) and "cv_job_match" in sb:
        breakdown["cvJobMatch"] = sb["cv_job_match"]

    # Recommendation badge
    score_100 = assessment.final_score or (assessment.score * 10 if assessment.score else None)
    if score_100 is not None:
        if score_100 >= 80:
            breakdown["recommendation"] = "STRONG HIRE"
            breakdown["recommendationColor"] = "green"
        elif score_100 >= 65:
            breakdown["recommendation"] = "HIRE"
            breakdown["recommendationColor"] = "blue"
        elif score_100 >= 50:
            breakdown["recommendation"] = "CONSIDER"
            breakdown["recommendationColor"] = "amber"
        else:
            breakdown["recommendation"] = "NOT RECOMMENDED"
            breakdown["recommendationColor"] = "red"

    return breakdown


def assessment_to_response(assessment: Assessment, db: Optional[Session] = None) -> Dict[str, Any]:
    """Serialize assessment to response dict.

    SECURITY POLICY:
    - Never expose raw server filesystem paths (`cv_file_url`).
    - Expose `assessment.token` only to authenticated recruiter/admin APIs so
      they can generate candidate assessment links.
    - Candidate runtime endpoints continue validating token possession.
    """
    candidate_name = ""
    candidate_email = ""
    if assessment.candidate:
        candidate_name = (assessment.candidate.full_name or assessment.candidate.email or "").strip()
        candidate_email = (assessment.candidate.email or "").strip()
    elif assessment.candidate_id and db:
        cand = db.query(Candidate).filter(Candidate.id == assessment.candidate_id).first()
        if cand:
            candidate_name = (cand.full_name or cand.email or "").strip()
            candidate_email = (cand.email or "").strip()
    if not candidate_name and candidate_email:
        candidate_name = candidate_email
    task_name = assessment.task.name if assessment.task else ""
    role_name = (assessment.role.name if getattr(assessment, "role", None) else "") or ""
    application_status = (assessment.application.status if getattr(assessment, "application", None) else None)
    evaluation_rubric = (assessment.task.evaluation_rubric if assessment.task else None) or {}
    evaluation_result = normalize_stored_evaluation_result(
        getattr(assessment, "manual_evaluation", None),
        assessment_id=assessment.id,
        completed_due_to_timeout=bool(getattr(assessment, "completed_due_to_timeout", False)),
        evaluation_rubric=evaluation_rubric,
    )

    # Derive a safe CV indicator (filename only, never the server path)
    cv_uploaded = bool(assessment.cv_file_url)

    data = {
        "id": assessment.id,
        "organization_id": assessment.organization_id,
        "candidate_id": assessment.candidate_id,
        "task_id": assessment.task_id,
        "role_id": getattr(assessment, "role_id", None),
        "application_id": getattr(assessment, "application_id", None),
        "token": assessment.token,  # Needed by recruiter UI to share assessment links
        "status": assessment.status.value if hasattr(assessment.status, "value") else str(assessment.status),
        "duration_minutes": assessment.duration_minutes,
        "started_at": assessment.started_at,
        "completed_at": assessment.completed_at,
        "expires_at": assessment.expires_at,
        "score": assessment.score,
        "tests_passed": assessment.tests_passed,
        "tests_total": assessment.tests_total,
        "code_quality_score": assessment.code_quality_score,
        "time_efficiency_score": assessment.time_efficiency_score,
        "ai_usage_score": assessment.ai_usage_score,
        "prompt_quality_score": assessment.prompt_quality_score,
        "prompt_efficiency_score": assessment.prompt_efficiency_score,
        "independence_score": assessment.independence_score,
        "context_utilization_score": assessment.context_utilization_score,
        "design_thinking_score": assessment.design_thinking_score,
        "debugging_strategy_score": assessment.debugging_strategy_score,
        "written_communication_score": assessment.written_communication_score,
        "learning_velocity_score": assessment.learning_velocity_score,
        "error_recovery_score": assessment.error_recovery_score,
        "requirement_comprehension_score": assessment.requirement_comprehension_score,
        "calibration_score": assessment.calibration_score,
        "prompt_fraud_flags": assessment.prompt_fraud_flags,
        "prompt_analytics": assessment.prompt_analytics,
        "browser_focus_ratio": assessment.browser_focus_ratio,
        "tab_switch_count": assessment.tab_switch_count,
        "time_to_first_prompt_seconds": assessment.time_to_first_prompt_seconds,
        # Never expose raw server path — only expose filename + flag
        "cv_uploaded": cv_uploaded,
        "cv_filename": assessment.cv_filename,
        "cv_uploaded_at": assessment.cv_uploaded_at,
        "cv_job_match_score": getattr(assessment, "cv_job_match_score", None),
        "cv_job_match_details": getattr(assessment, "cv_job_match_details", None),
        "final_score": assessment.final_score,
        "score_breakdown": assessment.score_breakdown,
        "score_weights_used": assessment.score_weights_used,
        "flags": assessment.flags,
        "scored_at": assessment.scored_at,
        "total_duration_seconds": assessment.total_duration_seconds,
        "total_prompts": assessment.total_prompts,
        "total_input_tokens": assessment.total_input_tokens,
        "total_output_tokens": assessment.total_output_tokens,
        "tests_run_count": assessment.tests_run_count,
        "tests_pass_count": assessment.tests_pass_count,
        "is_timer_paused": getattr(assessment, "is_timer_paused", False),
        "paused_at": getattr(assessment, "paused_at", None),
        "pause_reason": getattr(assessment, "pause_reason", None),
        "total_paused_seconds": getattr(assessment, "total_paused_seconds", 0),
        "completed_due_to_timeout": getattr(assessment, "completed_due_to_timeout", False),
        "ai_mode": getattr(assessment, "ai_mode", "claude_cli_terminal"),
        "cli_session_pid": getattr(assessment, "cli_session_pid", None),
        "cli_session_state": getattr(assessment, "cli_session_state", None),
        "cli_session_started_at": getattr(assessment, "cli_session_started_at", None),
        "cli_session_last_seen_at": getattr(assessment, "cli_session_last_seen_at", None),
        "cli_transcript": getattr(assessment, "cli_transcript", None),
        "final_repo_state": getattr(assessment, "final_repo_state", None),
        "git_evidence": getattr(assessment, "git_evidence", None),
        "assessment_repo_url": getattr(assessment, "assessment_repo_url", None),
        "assessment_branch": getattr(assessment, "assessment_branch", None),
        "clone_command": getattr(assessment, "clone_command", None),
        "posted_to_workable": assessment.posted_to_workable,
        "posted_to_workable_at": assessment.posted_to_workable_at,
        "invite_channel": getattr(assessment, "invite_channel", None),
        "invite_sent_at": getattr(assessment, "invite_sent_at", None),
        "credit_consumed_at": getattr(assessment, "credit_consumed_at", None),
        "candidate_cv_filename": (
            assessment.application.cv_filename if getattr(assessment, "application", None) and assessment.application.cv_filename
            else (assessment.candidate.cv_filename if assessment.candidate else None)
        ),
        "candidate_job_spec_filename": (
            assessment.role.job_spec_filename if getattr(assessment, "role", None) and assessment.role.job_spec_filename
            else (assessment.candidate.job_spec_filename if assessment.candidate else None)
        ),
        "candidate_cv_uploaded_at": (
            assessment.application.cv_uploaded_at if getattr(assessment, "application", None) and assessment.application.cv_uploaded_at
            else (assessment.candidate.cv_uploaded_at if assessment.candidate else None)
        ),
        "candidate_job_spec_uploaded_at": (
            assessment.role.job_spec_uploaded_at if getattr(assessment, "role", None) and assessment.role.job_spec_uploaded_at
            else (assessment.candidate.job_spec_uploaded_at if assessment.candidate else None)
        ),
        "test_results": assessment.test_results,
        "ai_prompts": assessment.ai_prompts,
        "timeline": assessment.timeline or build_timeline(assessment),
        "created_at": assessment.created_at,
        "prompts_list": build_prompts_list(assessment),
        "results": build_results(assessment),
        "breakdown": build_breakdown(assessment),
        "candidate_name": candidate_name,
        "candidate_email": candidate_email,
        "task_name": task_name,
        "role_name": role_name,
        "application_status": application_status,
        "evaluation_rubric": evaluation_rubric,
        "manual_evaluation": evaluation_result,
        "evaluation_result": evaluation_result,
        "is_demo": bool(getattr(assessment, "is_demo", False)),
        "demo_track": getattr(assessment, "demo_track", None),
        "demo_profile": getattr(assessment, "demo_profile", None),
    }
    return data
