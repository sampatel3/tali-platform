"""Wire-format adapter: Anthropic stream events -> AI SDK Data Stream Protocol.

The React side uses Vercel AI SDK's ``useChat`` (via ``ai@3.x``) — or the
``@assistant-ui/react`` adapter on top — both of which speak the v3 Data
Stream Protocol: newline-delimited tagged frames over
``Content-Type: text/event-stream``. Every line is ``<prefix>:<json>\\n``.

This adapter consumes Anthropic's streaming Messages API events and emits
the equivalent frames so the React side never has to know about
Anthropic's wire shape.

Reference: https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol#data-stream-protocol

Frames we emit (subset of the v3 protocol):
  ``f`` start-step                  ``{"messageId": "..."}``
  ``0`` text-delta                  string
  ``b`` tool-call-streaming-start   ``{toolCallId, toolName}``
  ``c`` tool-call-delta             ``{toolCallId, argsTextDelta}``
  ``9`` tool-call (complete)        ``{toolCallId, toolName, args}``
  ``a`` tool-result                 ``{toolCallId, result}``
  ``e`` finish-step                 ``{finishReason, usage, isContinued}``
  ``d`` finish-message              ``{finishReason, usage}``
  ``2`` data                        array of JSON (we use this to publish
                                    the conversation_id back to React)
  ``3`` error                       string

Earlier drafts of this file used ``9/a/b/c`` for the streaming-start /
delta / end / result pairs respectively — that order matched my reading
of an early spec but **not** the actual ``ai`` SDK runtime. The mapping
above is verified against the v3 useChat client.
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


def start_step(*, message_id: str) -> Frame:
    """``f:`` — emit at the start of every Anthropic round."""
    return Frame(_frame("f", {"messageId": message_id}))


def text_delta(delta: str) -> Frame:
    """``0:`` — one text token."""
    return Frame(_frame("0", delta))


def tool_call_streaming_start(*, tool_call_id: str, tool_name: str) -> Frame:
    """``b:`` — start of a streamed tool call. Emitted on Anthropic's
    ``content_block_start`` for a ``tool_use`` block."""
    return Frame(
        _frame(
            "b",
            {"toolCallId": tool_call_id, "toolName": tool_name},
        )
    )


def tool_call_delta(*, tool_call_id: str, args_text_delta: str) -> Frame:
    """``c:`` — partial JSON args delta during streaming."""
    return Frame(
        _frame(
            "c",
            {"toolCallId": tool_call_id, "argsTextDelta": args_text_delta},
        )
    )


def tool_call(*, tool_call_id: str, tool_name: str, args: dict[str, Any]) -> Frame:
    """``9:`` — final, complete tool call with parsed args. Emitted on
    Anthropic's ``content_block_stop`` for a ``tool_use`` block."""
    return Frame(
        _frame(
            "9",
            {"toolCallId": tool_call_id, "toolName": tool_name, "args": args},
        )
    )


def tool_result(*, tool_call_id: str, result: Any) -> Frame:
    """``a:`` — tool execution result (server-side dispatch). For errors,
    pass a result like ``{"error": "...", "tool": "..."}``; the v3 protocol
    doesn't have a dedicated ``isError`` flag at this level."""
    return Frame(
        _frame(
            "a",
            {"toolCallId": tool_call_id, "result": result},
        )
    )


def data(value: dict[str, Any]) -> Frame:
    """``2:`` — server-side data payload (e.g. conversation_id). Frontend
    has to opt in to read these via ``useChat``'s ``data`` field."""
    return Frame(_frame("2", [value]))


def progress(*, round_index: int) -> Frame:
    """Publish a truthful user-facing stage before a model round."""
    first = round_index == 0
    return data(
        {
            "progress": {
                "stage": "planning" if first else "synthesizing",
                "label": (
                    "Understanding your request and choosing the right search…"
                    if first
                    else "Reviewing the evidence and preparing your answer…"
                ),
            }
        }
    )


def error(message: str) -> Frame:
    """``3:`` — terminal error string."""
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
    """Map Anthropic stop reasons to AI SDK ``finishReason`` vocabulary."""
    if stop_reason in {"end_turn", "stop_sequence"}:
        return "stop"
    if stop_reason == "max_tokens":
        return "length"
    if stop_reason == "tool_use":
        return "tool-calls"
    if stop_reason in {"refusal", "pause_turn"}:
        return "content-filter"
    return "stop"
