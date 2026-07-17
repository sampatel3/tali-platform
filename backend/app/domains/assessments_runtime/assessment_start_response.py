"""Candidate start-response assembly after workspace providers have finished."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    resume_code_for_assessment,
    time_remaining_seconds,
    utcnow,
)
from ...models.assessment import Assessment
from ...models.candidate import Candidate
from ...models.organization import Organization
from ...models.task import Task
from ...services.task_spec_loader import candidate_rubric_view


def seed_assessment_opener(assessment: Assessment, task: Task) -> None:
    """Seed the task's deterministic interrogation opener exactly once."""
    from ...components.assessments import service as assessment_service

    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    raw_points = extra.get("decision_points")
    points = (
        [point for point in raw_points if isinstance(point, dict)]
        if isinstance(raw_points, list)
        else []
    )
    opener = assessment_service.render_opener(points) if points else ""
    if not opener or assessment.ai_prompts:
        return
    state = {
        str(point["id"]): {
            "status": "unaddressed",
            "raw_status": "unaddressed",
            "rationale": "",
        }
        for point in points
        if isinstance(point.get("id"), str) and point.get("id")
    }
    assessment.ai_prompts = [
        {
            "message": "",
            "response": opener,
            "opener": True,
            "transport": "task_opener",
            "timestamp": utcnow().isoformat(),
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls_made": [],
            "interrogation_state": state,
        }
    ]


def build_assessment_start_response(
    db: Session,
    assessment: Assessment,
    task: Task,
    *,
    sandbox_id: str,
    live_repo: dict[str, Any] | None,
    started_now: bool,
) -> dict[str, Any]:
    from ...components.assessments import service as assessment_service

    candidate_name = (
        db.query(Candidate.full_name)
        .filter(Candidate.id == assessment.candidate_id)
        .scalar()
    )
    organization_name = (
        db.query(Organization.name)
        .filter(Organization.id == assessment.organization_id)
        .scalar()
    )
    repo_structure = task.repo_structure if started_now or not live_repo else live_repo
    budget_limit = assessment_service.resolve_effective_budget_limit_usd(
        is_demo=bool(assessment.is_demo),
        task_budget_limit_usd=task.claude_budget_limit_usd,
    )
    claude_budget = assessment_service.build_claude_budget_snapshot(
        budget_limit_usd=budget_limit,
        prompts=assessment.ai_prompts or [],
    )
    ai_mode = assessment.ai_mode or "claude_cli_terminal"
    return {
        "assessment_id": assessment.id,
        "token": assessment.token,
        "sandbox_id": sandbox_id,
        "candidate_name": candidate_name,
        "organization_name": organization_name,
        "expires_at": assessment.expires_at,
        "invite_sent_at": assessment.invite_sent_at,
        "task": {
            "name": task.name,
            "description": task.description,
            "starter_code": resume_code_for_assessment(
                assessment,
                task.starter_code or "",
            ),
            "duration_minutes": assessment.duration_minutes,
            "task_key": task.task_key,
            "role": task.role,
            "scenario": task.scenario,
            "repo_structure": repo_structure,
            "rubric_categories": candidate_rubric_view(task.evaluation_rubric),
            "evaluation_rubric": None,
            "extra_data": None,
            "proctoring_enabled": (
                False
                if assessment_service.settings.MVP_DISABLE_PROCTORING
                else bool(task.proctoring_enabled)
            ),
            "claude_budget_limit_usd": budget_limit,
        },
        "claude_budget": claude_budget,
        "time_remaining": time_remaining_seconds(assessment),
        "is_timer_paused": bool(assessment.is_timer_paused),
        "pause_reason": assessment.pause_reason,
        "total_paused_seconds": int(assessment.total_paused_seconds or 0),
        "ai_mode": ai_mode,
        "terminal_mode": ai_mode == "claude_cli_terminal",
        "terminal_capabilities": assessment_service.terminal_capabilities(),
        "repo_url": assessment.assessment_repo_url,
        "branch_name": assessment.assessment_branch,
        "clone_command": assessment.clone_command,
        "ai_prompts": list(assessment.ai_prompts or []),
        "deliverable": (
            (task.extra_data or {}).get("deliverable")
            if isinstance(task.extra_data, dict)
            else None
        ),
    }


__all__ = ["build_assessment_start_response", "seed_assessment_opener"]
