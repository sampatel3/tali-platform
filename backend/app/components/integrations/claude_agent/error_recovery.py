"""Soft-recovery classification for ``claude-agent-sdk`` exceptions.

The SDK raises ``Exception`` when the bundled CLI emits a result with
``is_error=true`` — but several of those states are SOFT: the model
already produced useful text and used tools before the cap/error, and
we've captured that work in the agent service's stream loop. Hard-
failing those into "Please retry in a moment" throws candidate value
away (assessment 76, 2026-05-26: model had explained the bug then hit
max-turns, candidate saw the generic retry message).

This module owns the classification rules so the service file stays
under its 500-LOC arch gate and the recovery policy is reviewable in
one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# Substrings that mark an SDK exception as "soft" — the cap was reached
# but the model already did work we should surface. Case-insensitive
# match against ``str(exc)``. Order doesn't matter.
_SOFT_SIGNALS: tuple[str, ...] = (
    "maximum number of turns",  # SDK literal: "Reached maximum number of turns (N)"
    "error_max_turns",           # CLI subtype name some SDK versions surface
)

_SOFT_RECOVERY_TRAILER = (
    "\n\n_(Note: I hit my per-turn tool budget while answering. The "
    "above is what I can confirm; ask a more focused follow-up if "
    "you need me to dig deeper.)_"
)

# When the soft cap fires but the model produced ZERO text (only tool
# calls), the prior policy fell through to the generic retry message —
# unhelpful. Common case: "fix it" with multi-file edits, model burns
# all turns on read/write tools and never emits a narrative step
# (assessment 77, 2026-05-26).
_SOFT_NO_TEXT_TEMPLATE = (
    "I started working on this but hit my per-turn tool budget before "
    "I could finish. So far I made {tool_count} tool call(s){tool_summary}.\n\n"
    "Try one of:\n"
    "- Ask me to focus on **one file at a time** (e.g. \"fix dq/gate.py\").\n"
    "- Tell me the specific symptom you want addressed first.\n"
    "- Run the existing tests and paste the failure — I'll work from that."
)

_HARD_PARTIAL_TRAILER = (
    "\n\n_(Note: the chat service errored mid-response. The above "
    "is the partial answer — retry if needed.)_"
)

_GENERIC_RETRY_MESSAGE = "The chat service hit an error. Please retry in a moment."


@dataclass(frozen=True)
class RecoveredTurn:
    """Outcome of classifying an SDK exception against accumulated work.

    Fields mirror the subset of ``ChatTurn`` the service hands back so
    the caller just spreads them into the final ``ChatTurn`` construction.
    """

    success: bool
    content: str
    stop_reason: str


def is_soft_error(exc_text: str) -> bool:
    """True if ``str(exc)`` matches a known SOFT failure mode."""
    if not exc_text:
        return False
    lowered = exc_text.lower()
    return any(sig.lower() in lowered for sig in _SOFT_SIGNALS)


def _summarize_tool_calls(tool_calls: Sequence[dict] | None) -> str:
    """Format ``(Read(x.py), Edit(y.py))`` clause for the no-text
    soft-recovery message. Returns empty string when no useful summary
    can be extracted — the caller still names the count."""
    if not tool_calls:
        return ""
    names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        n = str(call.get("name", "")).split("__")[-1]
        inp = call.get("input") or {}
        target = inp.get("path") if isinstance(inp, dict) else None
        if n and target:
            names.append(f"{n}({target})")
        elif n:
            names.append(n)
    if not names:
        return ""
    preview = ", ".join(names[:4])
    if len(names) > 4:
        preview += ", …"
    return f" ({preview})"


def classify(
    exc_text: str,
    content_parts: Sequence[str],
    tool_calls: Sequence[dict] | None = None,
) -> RecoveredTurn:
    """Decide how to surface an SDK exception to the candidate.

    Four branches:
      1. Soft + content → success, content + soft trailer
         (``stop_reason="max_turns_soft"``).
      2. Soft + no text but has tool calls → success with a
         "made-progress, retry tighter" message naming the tools used
         (``stop_reason="max_turns_soft_no_text"``). Regression fix
         for the empty-text "fix it" case (#414 fell through to
         generic retry).
      3. Hard + content → failure but surface partial text
         (``stop_reason="sdk_exception_partial"``).
      4. Hard + no content → generic retry
         (``stop_reason="sdk_exception"``).
    """
    body = "\n".join(p for p in content_parts if p).strip()
    soft = is_soft_error(exc_text)

    if soft and body:
        return RecoveredTurn(
            success=True,
            content=body + _SOFT_RECOVERY_TRAILER,
            stop_reason="max_turns_soft",
        )

    if soft and tool_calls:
        return RecoveredTurn(
            success=True,
            content=_SOFT_NO_TEXT_TEMPLATE.format(
                tool_count=len(tool_calls),
                tool_summary=_summarize_tool_calls(tool_calls),
            ),
            stop_reason="max_turns_soft_no_text",
        )

    if body:
        return RecoveredTurn(
            success=False,
            content=body + _HARD_PARTIAL_TRAILER,
            stop_reason="sdk_exception_partial",
        )

    return RecoveredTurn(
        success=False,
        content=_GENERIC_RETRY_MESSAGE,
        stop_reason="sdk_exception",
    )
