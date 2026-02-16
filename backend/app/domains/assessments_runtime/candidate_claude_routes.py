from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ...components.assessments.claude_budget import (
    build_claude_budget_snapshot,
    compute_claude_cost_usd,
    resolve_effective_budget_limit_usd,
)
from ...components.assessments.repository import (
    append_assessment_timeline_event,
    ensure_utc,
    get_active_assessment,
    time_remaining_seconds,
    utcnow,
    validate_assessment_token,
)
from ...components.assessments.service import (
    enforce_active_or_timeout,
    enforce_not_paused,
    resume_assessment_timer,
)
from ...components.assessments.terminal_runtime import terminal_capabilities
from ...domains.integrations_notifications.adapters import build_claude_adapter
from ...models.task import Task
from ...platform.database import get_db
from ...schemas.assessment import ClaudeRequest

router = APIRouter()


@router.post("/{assessment_id}/claude")
def chat_with_claude(
    assessment_id: int,
    data: ClaudeRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    """Send a message to Claude AI assistant during assessment."""
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    enforce_active_or_timeout(assessment, db)
    enforce_not_paused(assessment)

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    effective_budget_limit = resolve_effective_budget_limit_usd(
        is_demo=bool(getattr(assessment, "is_demo", False)),
        task_budget_limit_usd=getattr(task, "claude_budget_limit_usd", None),
    )

    if getattr(assessment, "ai_mode", "legacy_chat") == "claude_cli_terminal":
        return {
            "success": False,
            "response": "This assessment uses Claude Code CLI in the terminal pane. Use the terminal instead of chat.",
            "content": "",
            "message": "This assessment uses Claude Code CLI in the terminal pane. Use the terminal instead of chat.",
            "is_timer_paused": bool(getattr(assessment, "is_timer_paused", False)),
            "pause_reason": getattr(assessment, "pause_reason", None),
            "time_remaining_seconds": time_remaining_seconds(assessment),
            "requires_terminal": True,
            "ai_mode": getattr(assessment, "ai_mode", "legacy_chat"),
            "terminal_capabilities": terminal_capabilities(),
            "claude_budget": build_claude_budget_snapshot(
                budget_limit_usd=effective_budget_limit,
                prompts=assessment.ai_prompts or [],
            ),
            "budget_exhausted": False,
        }

    claude_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=assessment.ai_prompts or [],
    )
    if claude_budget["enabled"] and claude_budget["is_exhausted"]:
        append_assessment_timeline_event(
            assessment,
            "ai_prompt_blocked_budget",
            {
                "used_usd": claude_budget["used_usd"],
                "limit_usd": claude_budget["limit_usd"],
                "tokens_used": claude_budget["tokens_used"],
            },
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
        return {
            "success": False,
            "response": "Claude budget limit reached for this task. Continue coding and submit when ready.",
            "content": "",
            "message": "Claude budget limit reached for this task. Continue coding and submit when ready.",
            "is_timer_paused": False,
            "pause_reason": None,
            "time_remaining_seconds": time_remaining_seconds(assessment),
            "requires_budget_top_up": True,
            "claude_budget": claude_budget,
            "budget_exhausted": True,
        }

    claude = build_claude_adapter()
    messages = data.conversation_history + [{"role": "user", "content": data.message}]

    t0 = time.time()
    response = claude.chat(messages)
    latency_ms = int((time.time() - t0) * 1000)
    claude_success = bool(response.get("success"))
    claude_text = (response.get("content", "") if claude_success else "") or ""
    input_tokens = max(0, int(response.get("input_tokens", 0) or 0))
    output_tokens = max(0, int(response.get("output_tokens", 0) or 0))
    tokens_used = max(0, int(response.get("tokens_used", 0) or 0))
    request_cost_usd = compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)

    prompt_record = {
        "message": data.message,
        "response": claude_text,
        "success": claude_success,
        "claude_outage": not claude_success,
        "timestamp": utcnow().isoformat(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": tokens_used,
        "request_cost_usd": round(request_cost_usd, 6),
        "response_latency_ms": latency_ms,
        "code_before": data.code_context or "",
        "code_after": "",
        "word_count": len(data.message.split()),
        "char_count": len(data.message),
        "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
        "paste_detected": data.paste_detected,
        "browser_focused": data.browser_focused,
    }

    if assessment.ai_prompts is None:
        assessment.ai_prompts = []

    prompts = list(assessment.ai_prompts)

    if prompts and data.code_context:
        prompts[-1] = {**prompts[-1], "code_after": data.code_context}

    prompts.append(prompt_record)
    updated_budget = build_claude_budget_snapshot(
        budget_limit_usd=effective_budget_limit,
        prompts=prompts,
    )
    prompts[-1] = {
        **prompts[-1],
        "claude_budget_used_usd": updated_budget["used_usd"],
        "claude_budget_remaining_usd": updated_budget["remaining_usd"],
    }
    assessment.ai_prompts = prompts

    append_assessment_timeline_event(
        assessment,
        "ai_prompt",
        {
            "word_count": prompt_record["word_count"],
            "char_count": prompt_record["char_count"],
            "input_tokens": prompt_record["input_tokens"],
            "output_tokens": prompt_record["output_tokens"],
            "response_latency_ms": prompt_record["response_latency_ms"],
            "paste_detected": prompt_record["paste_detected"],
            "browser_focused": prompt_record["browser_focused"],
            "time_since_last_prompt_ms": prompt_record["time_since_last_prompt_ms"],
            "request_cost_usd": round(request_cost_usd, 6),
            "claude_budget_used_usd": updated_budget["used_usd"],
            "claude_budget_remaining_usd": updated_budget["remaining_usd"],
            "claude_outage": not claude_success,
        },
    )

    if len(prompts) == 1 and assessment.started_at:
        started = ensure_utc(assessment.started_at)
        assessment.time_to_first_prompt_seconds = int((utcnow() - started).total_seconds())

    if not claude_success and not assessment.is_timer_paused:
        assessment.is_timer_paused = True
        assessment.paused_at = utcnow()
        assessment.pause_reason = "claude_outage"
        append_assessment_timeline_event(
            assessment,
            "timer_paused",
            {"pause_reason": "claude_outage"},
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to persist AI interaction")

    if not claude_success:
        return {
            "success": False,
            "response": "Claude is temporarily unavailable. Your timer is paused. Please retry in a moment.",
            "content": "",
            "message": "Claude is temporarily unavailable. Your timer is paused. Please retry in a moment.",
            "is_timer_paused": True,
            "pause_reason": assessment.pause_reason,
            "time_remaining_seconds": time_remaining_seconds(assessment),
            "requires_retry": True,
            "claude_budget": updated_budget,
            "budget_exhausted": bool(updated_budget["enabled"] and updated_budget["is_exhausted"]),
        }

    return {
        "success": True,
        "response": claude_text,
        "content": claude_text,
        "message": claude_text,
        "tokens_used": tokens_used,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "request_cost_usd": round(request_cost_usd, 6),
        "is_timer_paused": False,
        "pause_reason": None,
        "time_remaining_seconds": time_remaining_seconds(assessment),
        "claude_budget": updated_budget,
        "budget_exhausted": bool(updated_budget["enabled"] and updated_budget["is_exhausted"]),
    }


@router.post("/{assessment_id}/claude/retry")
def retry_claude_after_outage(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)

    if not assessment.is_timer_paused:
        return {
            "success": True,
            "message": "Assessment is not paused",
            "is_timer_paused": False,
            "time_remaining_seconds": time_remaining_seconds(assessment),
        }

    claude = build_claude_adapter()
    health = claude.chat(
        messages=[{"role": "user", "content": "Reply with OK."}],
        system="Reply with the single word OK.",
    )
    if not health.get("success"):
        return {
            "success": False,
            "message": "Claude is still unavailable",
            "is_timer_paused": True,
            "pause_reason": assessment.pause_reason,
            "time_remaining_seconds": time_remaining_seconds(assessment),
        }

    resume_assessment_timer(assessment, db, resume_reason="claude_retry_success")
    db.refresh(assessment)
    return {
        "success": True,
        "message": "Claude recovered and assessment resumed",
        "is_timer_paused": False,
        "time_remaining_seconds": time_remaining_seconds(assessment),
    }
