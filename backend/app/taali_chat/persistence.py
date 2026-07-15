"""Persistence boundaries for chat tool results.

The live model round and authenticated browser may need a sensitive result, but
that does not mean the same payload belongs in the long-lived conversation
transcript. Sensitive results are replaced with a small re-fetch marker before
the message row is written. The in-memory model loop still receives the real
payload for the current turn.
"""

from __future__ import annotations

from typing import Any

from .tool_registry import persistence_policy_for


def result_for_storage(tool_name: str, result: Any) -> Any:
    """Return the transcript-safe representation of a tool result."""

    try:
        policy = persistence_policy_for(tool_name)
    except KeyError:
        # Unknown tool errors contain no successful domain payload and are safe
        # to retain for debugging the conversation.
        policy = "standard"

    if policy == "standard":
        return result
    return {
        "tool": tool_name,
        "omitted_from_transcript": True,
        "persistence": policy,
        "message": (
            "Sensitive tool output was available for this turn but was not "
            "stored. Call the tool again if exact source data is needed."
        ),
    }


__all__ = ["result_for_storage"]
