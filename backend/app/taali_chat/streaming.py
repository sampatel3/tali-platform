"""Wire-format adapter: Anthropic stream events -> AI SDK Data Stream Protocol.

The frontend uses ``@assistant-ui/react`` (or Vercel AI SDK), both of which
speak the AI SDK Data Stream Protocol — newline-delimited tagged frames
over SSE. Every line looks like ``<type>:<json>\\n`` where ``type`` is a
single ASCII character or short string. This adapter consumes Anthropic's
streaming Messages API events and emits the equivalent frames so the
React side never has to touch Anthropic's wire shape directly.

Reference: https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol#data-stream-protocol

Frames we emit (subset of the protocol):
  ``f`` start-step (assistant message id)
  ``0`` text-delta (one token)
  ``9`` tool-call-start (tool_use block start)
  ``a`` tool-call-delta (per-token JSON args delta)
  ``b`` tool-call-end
  ``c`` tool-call-result (we emit one once we've run the tool)
  ``e`` finish-step (stop_reason + usage)
  ``d`` finish-message (terminal frame)
  ``2`` data (free-form JSON for our own server-side breadcrumbs:
        conversation_id assignments, citations, etc.)
  ``3`` error
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _frame(prefix: str, payload: Any) -> str:
    return f"{prefix}:{json.dumps(payload, ensure_ascii=False)}\n"


@dataclass(frozen=True)
class Frame:
    """A single AI-SDK protocol frame ready to write to the response."""

    body: str

    def encode(self) -> bytes:
        return self.body.encode("utf-8")


def text_delta(delta: str) -> Frame:
    return Frame(_frame("0", delta))


def tool_call_start(*, tool_call_id: str, tool_name: str) -> Frame:
    return Frame(
        _frame(
            "9",
            {
                "toolCallId": tool_call_id,
                "toolName": tool_name,
            },
        )
    )


def tool_call_delta(*, tool_call_id: str, args_delta: str) -> Frame:
    return Frame(
        _frame(
            "a",
            {
                "toolCallId": tool_call_id,
                "argsTextDelta": args_delta,
            },
        )
    )


def tool_call_end(*, tool_call_id: str, args: dict[str, Any]) -> Frame:
    return Frame(
        _frame(
            "b",
            {
                "toolCallId": tool_call_id,
                "args": args,
            },
        )
    )


def tool_call_result(*, tool_call_id: str, result: Any, is_error: bool = False) -> Frame:
    payload: dict[str, Any] = {
        "toolCallId": tool_call_id,
        "result": result,
    }
    if is_error:
        payload["isError"] = True
    return Frame(_frame("c", payload))


def data(value: dict[str, Any]) -> Frame:
    """Free-form server-side data. Use sparingly — frontend has to opt in."""
    return Frame(_frame("2", [value]))


def error(message: str) -> Frame:
    return Frame(_frame("3", message))


def finish_step(*, stop_reason: str | None, usage: dict[str, int] | None) -> Frame:
    return Frame(
        _frame(
            "e",
            {
                "finishReason": _normalize_stop(stop_reason),
                "usage": usage or {"promptTokens": 0, "completionTokens": 0},
                "isContinued": False,
            },
        )
    )


def finish_message(*, stop_reason: str | None, usage: dict[str, int] | None) -> Frame:
    return Frame(
        _frame(
            "d",
            {
                "finishReason": _normalize_stop(stop_reason),
                "usage": usage or {"promptTokens": 0, "completionTokens": 0},
            },
        )
    )


def _normalize_stop(stop_reason: str | None) -> str:
    """Map Anthropic stop reasons to AI SDK finishReason vocabulary."""
    if stop_reason in {"end_turn", "stop_sequence"}:
        return "stop"
    if stop_reason == "max_tokens":
        return "length"
    if stop_reason == "tool_use":
        return "tool-calls"
    if stop_reason in {"refusal", "pause_turn"}:
        return "content-filter"
    return "stop"
