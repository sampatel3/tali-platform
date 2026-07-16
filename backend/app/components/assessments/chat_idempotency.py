"""Durable response replay for candidate assessment chat requests."""

from __future__ import annotations

from typing import Any

from .claude_budget import build_claude_budget_snapshot


class RequestIdConflictError(ValueError):
    pass


def replay_candidate_chat_request(
    *,
    prompts: list[dict[str, Any]],
    request_id: str | None,
    message: str,
    budget_limit_usd: float | None,
) -> dict[str, Any] | None:
    """Return a committed response for ``request_id``, or ``None`` if new."""

    if not request_id:
        return None
    prior_request = next(
        (
            record
            for record in reversed(prompts)
            if isinstance(record, dict)
            and str(record.get("request_id") or "") == request_id
        ),
        None,
    )
    if prior_request is None:
        return None
    if str(prior_request.get("message") or "").strip() != message.strip():
        raise RequestIdConflictError(
            "request_id was already used for a different message"
        )
    return {
        "content": str(prior_request.get("response") or ""),
        "tool_calls_made": list(prior_request.get("tool_calls_made") or []),
        "input_tokens": int(prior_request.get("input_tokens") or 0),
        "output_tokens": int(prior_request.get("output_tokens") or 0),
        "latency_ms": int(prior_request.get("response_latency_ms") or 0),
        "claude_budget": build_claude_budget_snapshot(
            budget_limit_usd=budget_limit_usd,
            prompts=prompts,
        ),
        "assessment_voided": bool(prior_request.get("assessment_voided", False)),
        "request_id": request_id,
        "idempotent_replay": True,
    }


__all__ = ["RequestIdConflictError", "replay_candidate_chat_request"]
