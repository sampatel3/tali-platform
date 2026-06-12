"""Repair tool_use/tool_result pairing in replayed chat history.

Both chat engines (agent_chat, taali_chat) persist the assistant tool_use turn
and the user tool_result turn as two separate writes. If a turn is interrupted
between them — crash, timeout, deploy mid-turn — the stored history is left with
a tool_use that has no following tool_result, and Anthropic 400s the WHOLE
conversation on the next replay ("tool_use ids were found without tool_result
blocks"). One poisoned turn would otherwise brick the whole chat thread.

``sanitize_tool_pairs`` guarantees every assistant tool_use is immediately
followed by a user message carrying a tool_result for each id — synthesising an
error result for any missing one — so replay can never 400 on a dangling pair.
"""
from __future__ import annotations

from typing import Any

_PLACEHOLDER = "[result unavailable — the earlier turn was interrupted]"


def sanitize_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        content = msg.get("content")
        if msg.get("role") == "assistant" and isinstance(content, list):
            tool_use_ids = [
                b["id"]
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
            ]
            if tool_use_ids:
                out.append(msg)
                nxt = messages[i + 1] if i + 1 < n else None
                nxt_results = (
                    isinstance(nxt, dict)
                    and nxt.get("role") == "user"
                    and isinstance(nxt.get("content"), list)
                    and any(
                        isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in nxt["content"]
                    )
                )
                existing = list(nxt["content"]) if nxt_results else []
                provided = {
                    b.get("tool_use_id")
                    for b in existing
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                }
                synth = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": _PLACEHOLDER,
                        "is_error": True,
                    }
                    for tid in tool_use_ids
                    if tid not in provided
                ]
                out.append({"role": "user", "content": synth + existing})
                i += 2 if nxt_results else 1
                continue
        out.append(msg)
        i += 1
    return out
