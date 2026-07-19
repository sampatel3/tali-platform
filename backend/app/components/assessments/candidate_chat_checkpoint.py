"""JSON-safe checkpoints for completed candidate-chat provider turns."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

_CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class CandidateChatTurnCheckpoint:
    """Provider result fields needed by deterministic DB finalization."""

    success: bool
    stop_reason: str | None
    content: str
    tool_calls_made: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    model: str


@dataclass(frozen=True)
class CandidateChatFinalizationInput:
    """Exact bounded request fields consumed by deterministic finalization."""

    message: str
    code_context: str | None
    selected_file_path: str | None
    paste_detected: bool
    browser_focused: bool
    time_since_last_prompt_ms: int | None
    request_id: str | None


def serialize_candidate_chat_turn(chat_turn: Any) -> dict[str, Any]:
    """Return the bounded provider result shape stored in prompt analytics."""

    return {
        "version": _CHECKPOINT_VERSION,
        "success": bool(getattr(chat_turn, "success", True)),
        "stop_reason": (
            str(getattr(chat_turn, "stop_reason", "") or "").strip() or None
        ),
        "content": str(getattr(chat_turn, "content", "") or ""),
        "tool_calls_made": deepcopy(
            list(getattr(chat_turn, "tool_calls_made", []) or [])
        ),
        "input_tokens": max(int(getattr(chat_turn, "input_tokens", 0) or 0), 0),
        "output_tokens": max(
            int(getattr(chat_turn, "output_tokens", 0) or 0), 0
        ),
        "cache_read_input_tokens": max(
            int(getattr(chat_turn, "cache_read_input_tokens", 0) or 0), 0
        ),
        "cache_creation_input_tokens": max(
            int(getattr(chat_turn, "cache_creation_input_tokens", 0) or 0), 0
        ),
        "model": str(getattr(chat_turn, "model", "") or ""),
    }


def serialize_candidate_chat_input(value: Any) -> dict[str, Any]:
    """Persist the original request shape needed after process/request loss."""

    return {
        "version": _CHECKPOINT_VERSION,
        "message": str(getattr(value, "message", "") or "").strip(),
        "code_context": (
            str(getattr(value, "code_context", "") or "")[:12000] or None
        ),
        "selected_file_path": (
            str(getattr(value, "selected_file_path", "") or "").strip() or None
        ),
        "paste_detected": bool(getattr(value, "paste_detected", False)),
        "browser_focused": bool(getattr(value, "browser_focused", True)),
        "time_since_last_prompt_ms": getattr(
            value, "time_since_last_prompt_ms", None
        ),
        "request_id": (
            str(getattr(value, "request_id", "") or "").strip() or None
        ),
    }


def restore_candidate_chat_input(value: Any) -> CandidateChatFinalizationInput:
    """Restore the exact finalization input; reject incomplete old evidence."""

    if not isinstance(value, dict) or value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Candidate chat finalization input is missing or unsupported")
    message = value.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Candidate chat finalization input is malformed")
    elapsed = value.get("time_since_last_prompt_ms")
    if elapsed is not None and not isinstance(elapsed, int):
        raise ValueError("Candidate chat finalization timing input is malformed")
    return CandidateChatFinalizationInput(
        message=message.strip(),
        code_context=(
            str(value.get("code_context") or "")[:12000] or None
        ),
        selected_file_path=(
            str(value.get("selected_file_path") or "").strip() or None
        ),
        paste_detected=bool(value.get("paste_detected", False)),
        browser_focused=bool(value.get("browser_focused", True)),
        time_since_last_prompt_ms=elapsed,
        request_id=(str(value.get("request_id") or "").strip() or None),
    )


def restore_candidate_chat_turn(value: Any) -> CandidateChatTurnCheckpoint:
    """Validate and restore a durable provider result without another call."""

    if not isinstance(value, dict) or value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Candidate chat provider checkpoint is missing or unsupported")
    success = value.get("success")
    stop_reason = value.get("stop_reason")
    content = value.get("content")
    tool_calls = value.get("tool_calls_made")
    if (
        not isinstance(success, bool)
        or (stop_reason is not None and not isinstance(stop_reason, str))
        or not isinstance(content, str)
        or not isinstance(tool_calls, list)
    ):
        raise ValueError("Candidate chat provider checkpoint is malformed")
    if not all(isinstance(item, dict) for item in tool_calls):
        raise ValueError("Candidate chat provider checkpoint tool calls are malformed")
    return CandidateChatTurnCheckpoint(
        success=success,
        stop_reason=str(stop_reason).strip() or None if stop_reason is not None else None,
        content=content,
        tool_calls_made=deepcopy(tool_calls),
        input_tokens=max(int(value.get("input_tokens") or 0), 0),
        output_tokens=max(int(value.get("output_tokens") or 0), 0),
        cache_read_input_tokens=max(
            int(value.get("cache_read_input_tokens") or 0), 0
        ),
        cache_creation_input_tokens=max(
            int(value.get("cache_creation_input_tokens") or 0), 0
        ),
        model=str(value.get("model") or ""),
    )


__all__ = [
    "CandidateChatFinalizationInput",
    "CandidateChatTurnCheckpoint",
    "restore_candidate_chat_input",
    "restore_candidate_chat_turn",
    "serialize_candidate_chat_input",
    "serialize_candidate_chat_turn",
]
