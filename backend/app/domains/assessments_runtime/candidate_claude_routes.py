from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ...components.assessments.repository import (
    append_assessment_timeline_event,
    get_active_assessment,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.claude_budget import (
    build_claude_budget_snapshot,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.terminal_runtime import terminal_capabilities, terminal_env
from ...components.integrations.claude.service import ClaudeService
from ...models.organization import Organization
from ...models.task import Task
from ...platform.database import get_db
from ...schemas.assessment import ClaudeRequest

router = APIRouter()

_MAX_HISTORY_MESSAGES = 20
_MAX_CONTEXT_CHARS = 12000
_MAX_REPO_FILES_IN_PROMPT = 120


def _normalize_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in history or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content[:4000]})
    if len(normalized) > _MAX_HISTORY_MESSAGES:
        return normalized[-_MAX_HISTORY_MESSAGES:]
    return normalized


def _repo_outline(task: Task) -> str:
    repo_structure = getattr(task, "repo_structure", None)
    if not isinstance(repo_structure, dict):
        return "No repository structure available."
    files = repo_structure.get("files")
    if not isinstance(files, dict):
        return "No repository files available."

    paths = sorted(str(path) for path in files.keys() if str(path).strip())
    if not paths:
        return "No repository files available."
    paths = paths[:_MAX_REPO_FILES_IN_PROMPT]
    return "\n".join(f"- {path}" for path in paths)


def _build_system_prompt(task: Task, data: ClaudeRequest) -> str:
    scenario = str(getattr(task, "scenario", "") or "").strip()
    description = str(getattr(task, "description", "") or "").strip()
    task_context = scenario or description or "Task context not provided."
    repo_tree = _repo_outline(task)
    code_context = str(data.code_context or "").strip()
    if code_context:
        code_context = code_context[:_MAX_CONTEXT_CHARS]
    else:
        code_context = "No editor snapshot provided."

    return (
        "You are a coding assistant in TAALI's assessment workspace. "
        "Behave like a Cursor-style coding assistant: be concrete, actionable, and concise. "
        "Do not fabricate files or commands. If you reference files, use only file paths from the repository tree.\n\n"
        f"Task context:\n{task_context}\n\n"
        f"Repository files:\n{repo_tree}\n\n"
        f"Current editor snapshot:\n{code_context}"
    )


def _resolve_assessment_api_key(org: Organization | None) -> str:
    env = terminal_env(org)
    return str(env.get("ANTHROPIC_API_KEY") or "").strip()


@router.post("/{assessment_id}/claude")
def chat_with_claude(
    assessment_id: int,
    data: ClaudeRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    org = db.query(Organization).filter(Organization.id == assessment.organization_id).first()
    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )
    api_key = _resolve_assessment_api_key(org)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Claude API key is not configured for this workspace.",
                "requires_terminal": False,
                "terminal_capabilities": terminal_capabilities(),
            },
        )

    history = _normalize_history(data.conversation_history)
    message = str(data.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if not history or history[-1].get("role") != "user" or history[-1].get("content") != message:
        history.append({"role": "user", "content": message})

    system_prompt = _build_system_prompt(task, data)
    claude = ClaudeService(api_key)
    started_at = time.perf_counter()
    result = claude.chat(messages=history, system=system_prompt)
    latency_ms = int((time.perf_counter() - started_at) * 1000)

    if not result.get("success"):
        append_assessment_timeline_event(
            assessment,
            "ai_prompt_error",
            {"latency_ms": latency_ms},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "Claude request failed. Please retry."},
        )

    response_text = str(result.get("content") or "").strip()
    input_tokens = max(0, int(result.get("input_tokens") or 0))
    output_tokens = max(0, int(result.get("output_tokens") or 0))

    prompts = list(getattr(assessment, "ai_prompts", None) or [])
    is_first_prompt = len(prompts) == 0
    prompts.append(
        {
            "message": message,
            "response": response_text,
            "code_context": str(data.code_context or "")[:_MAX_CONTEXT_CHARS],
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "time_since_assessment_start_ms": data.time_since_assessment_start_ms,
            "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
            "response_latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "timestamp": utcnow().isoformat(),
        }
    )
    assessment.ai_prompts = prompts
    assessment.total_input_tokens = int(getattr(assessment, "total_input_tokens", 0) or 0) + input_tokens
    assessment.total_output_tokens = int(getattr(assessment, "total_output_tokens", 0) or 0) + output_tokens
    append_assessment_timeline_event(
        assessment,
        "ai_prompt",
        {
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "time_since_assessment_start_ms": data.time_since_assessment_start_ms,
            "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
        },
    )
    if is_first_prompt:
        append_assessment_timeline_event(
            assessment,
            "first_prompt",
            {
                "preview": message[:120],
            },
        )
    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=prompts,
    )
    db.commit()

    return {
        "success": True,
        "content": response_text,
        "response": response_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": input_tokens + output_tokens,
        "latency_ms": latency_ms,
        "claude_budget": claude_budget,
    }


@router.post("/{assessment_id}/claude/retry")
def retry_claude_after_outage(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    return {
        "success": True,
        "message": "Claude is available. Submit a new prompt.",
    }
