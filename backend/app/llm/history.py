"""Bounded, replay-safe model history with a durable full transcript.

Chat transcripts remain complete in the database and in the recruiter UI.  The
model, however, should not receive an ever-growing copy on every paid call.
This module keeps a recent window, treats tool-use/result pairs atomically, and
renders a bounded excerpt of the omitted dialogue as user-role context. The
excerpt can contain recruiter-supplied text, so it must never be promoted into
a higher-authority system block.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .tool_pairs import sanitize_tool_pairs


@dataclass(frozen=True)
class HistoryWindow:
    messages: list[dict[str, Any]]
    earlier_excerpt: str | None
    omitted_messages: int = 0


def _message_size(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False, default=str))


def _is_tool_use(message: dict[str, Any]) -> bool:
    return message.get("role") == "assistant" and any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in (message.get("content") or [])
    )


def _is_tool_result(message: dict[str, Any]) -> bool:
    return message.get("role") == "user" and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in (message.get("content") or [])
    )


def _atomic_groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Keep an assistant tool request beside its synthetic result message."""

    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if (
            _is_tool_use(message)
            and index + 1 < len(messages)
            and _is_tool_result(messages[index + 1])
        ):
            groups.append([message, messages[index + 1]])
            index += 2
        else:
            groups.append([message])
            index += 1
    return groups


def _text_excerpt(message: dict[str, Any], *, per_message_chars: int = 800) -> str | None:
    role = "Recruiter" if message.get("role") == "user" else "Assistant"
    parts: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]))
        elif block.get("type") == "tool_use" and block.get("name"):
            # Tool results can contain whole CVs and other large/untrusted
            # records. Keep the request name for continuity; live data should
            # be re-read with the tool instead of replayed from stale output.
            parts.append(f"[used tool {block['name']}]")
    text = " ".join(" ".join(parts).split())
    if not text:
        return None
    if len(text) > per_message_chars:
        text = text[: per_message_chars - 1].rstrip() + "…"
    return f"{role}: {text}"


def _earlier_excerpt(
    messages: list[dict[str, Any]], *, max_chars: int
) -> str | None:
    # Prefer the most recent omitted dialogue because it connects directly to
    # the retained window. Build backwards, then restore chronological order.
    selected: list[str] = []
    used = 0
    for message in reversed(messages):
        line = _text_excerpt(message)
        if not line:
            continue
        cost = len(line) + 1
        if selected and used + cost > max_chars:
            break
        if cost > max_chars:
            line = line[: max(1, max_chars - 1)].rstrip() + "…"
            cost = len(line)
        selected.append(line)
        used += cost
    if not selected:
        return None
    selected.reverse()
    return (
        "EARLIER CONVERSATION EXCERPT (the full transcript remains stored; "
        "re-read live product data with tools rather than relying on stale results):\n"
        + "\n".join(selected)
    )


def bounded_history(
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
    max_chars: int,
    excerpt_chars: int,
) -> HistoryWindow:
    """Return a bounded recent replay window plus an older-text excerpt.

    At least the newest atomic group is retained even if a single message is
    larger than ``max_chars``. This guarantees that the just-persisted user
    request is never discarded. Inputs are copied by the sanitiser/list slices;
    the durable transcript is not mutated.
    """

    if max_messages < 1 or max_chars < 1 or excerpt_chars < 1:
        raise ValueError("history limits must be positive")
    repaired = sanitize_tool_pairs(list(messages))
    groups = _atomic_groups(repaired)
    kept_reversed: list[list[dict[str, Any]]] = []
    kept_count = 0
    kept_chars = 0
    for group in reversed(groups):
        group_count = len(group)
        group_chars = sum(_message_size(message) for message in group)
        would_overflow = (
            kept_reversed
            and (
                kept_count + group_count > max_messages
                or kept_chars + group_chars > max_chars
            )
        )
        if would_overflow:
            break
        kept_reversed.append(group)
        kept_count += group_count
        kept_chars += group_chars

    kept_groups = list(reversed(kept_reversed))
    kept = [message for group in kept_groups for message in group]
    omitted_count = len(repaired) - len(kept)
    omitted = repaired[:omitted_count]
    return HistoryWindow(
        messages=kept,
        earlier_excerpt=_earlier_excerpt(omitted, max_chars=excerpt_chars),
        omitted_messages=omitted_count,
    )


def model_history_messages(window: HistoryWindow) -> list[dict[str, Any]]:
    """Build model replay without elevating untrusted old dialogue.

    Anthropic coalesces adjacent messages with the same role, so a retained
    window that begins with a user turn remains valid. Keeping the excerpt at
    user authority prevents an old prompt-injection attempt from becoming a
    system instruction merely because it aged out of the recent window.
    """

    messages = list(window.messages)
    if not window.earlier_excerpt:
        return messages
    return [
        {
            "role": "user",
            "content": [{"type": "text", "text": window.earlier_excerpt}],
        },
        *messages,
    ]


__all__ = ["HistoryWindow", "bounded_history", "model_history_messages"]
