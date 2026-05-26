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


def classify(
    exc_text: str,
    content_parts: Sequence[str],
) -> RecoveredTurn:
    """Decide how to surface an SDK exception to the candidate.

    Three branches:
      1. Soft signal + non-empty ``content_parts`` →
         success=True, partial content + soft trailer
         (``stop_reason="max_turns_soft"``).
      2. Hard exception + non-empty ``content_parts`` →
         success=False, partial content + hard trailer
         (``stop_reason="sdk_exception_partial"``).
      3. Hard exception + empty ``content_parts`` →
         success=False, generic retry message
         (``stop_reason="sdk_exception"``).
    """
    body = "\n".join(p for p in content_parts if p).strip()

    if is_soft_error(exc_text) and body:
        return RecoveredTurn(
            success=True,
            content=body + _SOFT_RECOVERY_TRAILER,
            stop_reason="max_turns_soft",
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
