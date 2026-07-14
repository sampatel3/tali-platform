"""One Anthropic streaming round for the chat orchestrator.

Extracted from ``taali_chat.service`` to keep that module under the
500-LOC architecture gate. ``_stream_one_round`` drives a single
``client.messages.stream(...)`` call: it yields AI-SDK protocol frames as
deltas arrive and returns ``(blocks, stop_reason, usage)`` for the caller
to fold into the running turn. The helpers here are SDK-shape plumbing —
no DB, no persistence; ``service.run_chat_turn`` owns the loop and side
effects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

from anthropic import Anthropic

from . import streaming
from .tool_registry import TAALI_CHAT_TOOLS


# Cap on tokens per turn — protects against runaway responses; 4k is large
# enough for a comparison table + commentary.
MAX_TOKENS_PER_TURN = 4096


@dataclass
class _RunningUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def _stream_one_round(
    *,
    client: Anthropic,
    model: str,
    messages: list[dict[str, Any]],
    system: list[dict[str, Any]],
    metering: dict[str, Any],
) -> Iterator[streaming.Frame]:
    """Stream one Anthropic call. Yields frames; returns (blocks, stop, usage)."""
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS_PER_TURN,
        system=system,
        tools=TAALI_CHAT_TOOLS,
        messages=messages,
        # The metered wrapper writes this paid round in its own committed
        # session, including interrupted streams when the SDK exposes usage.
        metering=metering,
    ) as stream:
        # Per-block accumulator for tool_use input JSON (Anthropic streams
        # arguments as ``input_json`` partial deltas; we have to glue them
        # back into a dict for the AI-SDK ``b`` frame).
        tool_args_buffer: dict[str, str] = {}
        tool_names: dict[str, str] = {}

        for event in stream:
            etype = getattr(event, "type", None)

            if etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if block is None:
                    continue
                if getattr(block, "type", None) == "tool_use":
                    tool_id = block.id
                    tool_args_buffer[tool_id] = ""
                    tool_names[tool_id] = block.name
                    yield streaming.tool_call_streaming_start(
                        tool_call_id=tool_id, tool_name=block.name
                    )

            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    yield streaming.text_delta(delta.text)
                elif dtype == "input_json_delta":
                    block_index = getattr(event, "index", None)
                    # Match the running tool_use block by index → id.
                    tool_id = _tool_id_at_index(stream, block_index)
                    if tool_id is None:
                        continue
                    partial = delta.partial_json or ""
                    tool_args_buffer[tool_id] = tool_args_buffer.get(tool_id, "") + partial
                    yield streaming.tool_call_delta(
                        tool_call_id=tool_id, args_text_delta=partial
                    )

            elif etype == "content_block_stop":
                block_index = getattr(event, "index", None)
                tool_id = _tool_id_at_index(stream, block_index)
                if tool_id is not None and tool_id in tool_args_buffer:
                    raw = tool_args_buffer.get(tool_id, "")
                    try:
                        args = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        args = {}
                    name = tool_names.get(tool_id, "")
                    yield streaming.tool_call(
                        tool_call_id=tool_id, tool_name=name, args=args
                    )

        # Final message snapshot.
        final = stream.get_final_message()

    blocks = [_block_to_dict(b) for b in final.content]
    usage = _RunningUsage(
        input_tokens=int(getattr(final.usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(final.usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(final.usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(
            getattr(final.usage, "cache_creation_input_tokens", 0) or 0
        ),
    )
    return blocks, final.stop_reason, usage


def _tool_id_at_index(stream, index: int | None) -> str | None:
    """Look up the running tool_use block id by its position in the stream."""
    if index is None:
        return None
    try:
        message = stream.current_message_snapshot
    except Exception:  # pragma: no cover — older SDKs
        return None
    blocks = getattr(message, "content", []) or []
    if 0 <= index < len(blocks):
        block = blocks[index]
        if getattr(block, "type", None) == "tool_use":
            return getattr(block, "id", None)
    return None


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Anthropic SDK content blocks → plain JSON-safe dicts for persistence."""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input or {},
        }
    if btype == "thinking":
        return {"type": "thinking", "thinking": getattr(block, "thinking", "")}
    # Fallback: model_dump if pydantic, else str()
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": btype or "unknown", "raw": str(block)}
