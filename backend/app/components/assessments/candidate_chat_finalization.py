"""Atomic candidate-chat persistence after detached provider work."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from .chat_idempotency import compact_completed_candidate_chat_claim
from .integrity import (
    OFF_TASK,
    REFUSAL_MESSAGE,
    VOID_MESSAGE,
    WARN_MESSAGE,
    classify_turn,
    count_misuse,
    decide_action,
    strip_refusal_marker,
)
from .repository import append_assessment_timeline_event, utcnow

_MAX_CONTEXT_CHARS = 12000


def finalize_candidate_chat_turn(
    *,
    db: Session,
    prepared: Any,
    token: str,
    data: Any,
    chat_turn: Any,
    latency_ms: int,
    persist_state: dict[str, Any],
    build_budget_snapshot: Callable[..., dict[str, Any]],
    load_authority: Callable[..., tuple[Any, dict[str, Any]]],
) -> dict[str, Any]:
    """Revalidate exact authority and persist response, integrity, and claim."""

    if not bool(getattr(chat_turn, "success", True)):
        raise ValueError("Unsuccessful provider evidence cannot be finalized")
    assessment, _claim = load_authority(db, prepared, token)
    prompts = list(assessment.ai_prompts or [])
    message = data.message.strip()
    is_first_prompt = not any(
        isinstance(record, dict) and str(record.get("message") or "").strip()
        for record in prompts
    )
    misuse_category = classify_turn(message, chat_turn.content)
    if misuse_category == OFF_TASK:
        response_content = strip_refusal_marker(chat_turn.content)
    elif misuse_category:
        response_content = REFUSAL_MESSAGE
    else:
        response_content = chat_turn.content

    voided = False
    if misuse_category:
        misuse_count = count_misuse(prompts) + 1
        integrity_action = decide_action(misuse_count)
        flags = list(assessment.prompt_fraud_flags or [])
        flags.append(
            {
                "type": f"misuse_{misuse_category}",
                "prompt_index": len(prompts),
                "confidence": 1.0,
                "evidence": misuse_category,
            }
        )
        assessment.prompt_fraud_flags = flags
        append_assessment_timeline_event(
            assessment,
            "integrity_flag",
            {
                "category": misuse_category,
                "count": misuse_count,
                "action": integrity_action,
            },
        )
        if integrity_action == "warn":
            response_content = f"{response_content}{WARN_MESSAGE}"
        elif integrity_action == "void":
            response_content = VOID_MESSAGE
            voided = True
            assessment.is_voided = True
            assessment.voided_at = utcnow()
            assessment.void_reason = (
                f"Auto-voided: repeated assessment misuse ({misuse_category}); "
                f"{misuse_count} flagged attempts."
            )

    tool_calls = list(getattr(chat_turn, "tool_calls_made", []) or [])
    input_tokens = int(getattr(chat_turn, "input_tokens", 0) or 0)
    output_tokens = int(getattr(chat_turn, "output_tokens", 0) or 0)
    prompts.append(
        {
            "message": message,
            "response": response_content,
            "misuse": misuse_category,
            "code_context": str(data.code_context or "")[:_MAX_CONTEXT_CHARS],
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "time_since_last_prompt_ms": data.time_since_last_prompt_ms,
            "response_latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": int(
                getattr(chat_turn, "cache_read_input_tokens", 0) or 0
            ),
            "cache_creation_input_tokens": int(
                getattr(chat_turn, "cache_creation_input_tokens", 0) or 0
            ),
            "model": str(getattr(chat_turn, "model", "") or ""),
            "provider_success": True,
            "stop_reason": (
                str(getattr(chat_turn, "stop_reason", "") or "").strip() or None
            ),
            "timestamp": utcnow().isoformat(),
            "tool_calls_made": tool_calls,
            "transport": "claude_agent_sdk",
            "request_id": prepared.request_id,
            "request_hash": prepared.request_hash,
            "assessment_voided": voided,
            "interrogation_state": persist_state,
        }
    )
    assessment.ai_prompts = prompts
    assessment.total_input_tokens = int(assessment.total_input_tokens or 0) + input_tokens
    assessment.total_output_tokens = int(assessment.total_output_tokens or 0) + output_tokens
    append_assessment_timeline_event(
        assessment,
        "ai_prompt",
        {
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": len(tool_calls),
            "paste_detected": bool(data.paste_detected),
            "browser_focused": bool(data.browser_focused),
            "transport": "claude_agent_sdk",
        },
    )
    if is_first_prompt:
        append_assessment_timeline_event(
            assessment, "first_prompt", {"preview": message[:120]}
        )
    completed_at = utcnow().isoformat()
    assessment.prompt_analytics = compact_completed_candidate_chat_claim(
        assessment.prompt_analytics,
        claim_key=prepared.claim_key,
        request_hash=prepared.request_hash,
        completed_at=completed_at,
        stop_reason=(
            str(getattr(chat_turn, "stop_reason", "") or "").strip() or None
        ),
    )
    budget = build_budget_snapshot(
        budget_limit_usd=prepared.budget_limit_usd,
        prompts=prompts,
    )
    db.commit()
    return {
        "content": response_content,
        "tool_calls_made": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "claude_budget": budget,
        "assessment_voided": voided,
        "request_id": prepared.request_id,
        "idempotent_replay": False,
    }


__all__ = ["finalize_candidate_chat_turn"]
