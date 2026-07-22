"""Deterministic grounding requirements for model-facing recruiting reads.

Prompts improve tool selection, but they cannot be the final trust boundary.
This module classifies requests whose answer necessarily depends on durable
candidate-action history.  Chat transports use the classification to withhold
an unsupported terminal answer unless a successful canonical read supplied the
required capability in the same turn.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .catalog import (
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_POOL_STATE,
)


ACTION_HISTORY_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's confirmed candidate-action "
    "history, so I won't claim a result. Please try again."
)
POOL_STATE_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's canonical candidate pool and "
    "current state, so I won't claim a result. Please try again."
)
DECISION_HISTORY_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's agent-decision history, so I "
    "won't claim a result. Please try again."
)
GROUNDING_REQUIRED_MESSAGE = (
    "I couldn't verify that from this role's canonical candidate data, so I "
    "won't claim a result. Please try again."
)

_COMPLETED_ACTION_RE = re.compile(
    r"\b(?:advance(?:d)?|reject(?:ed)?|hire(?:d)?|withdrew|withdrawn|"
    r"move(?:d)?|sent|resent|invite(?:d)?)\b",
    re.IGNORECASE,
)
_HISTORY_MARKER_RE = re.compile(
    r"\b(?:did|have|has|had|was|were|when|history|historical|recently|"
    r"today|yesterday|ago|earlier|previously|before|after|between|"
    r"last\s+(?:day|week|month|quarter|year)|"
    r"this\s+(?:day|week|month|quarter|year)|"
    r"past\s+(?:day|week|month|quarter|year))\b",
    re.IGNORECASE,
)
_CANDIDATE_CONTEXT_RE = re.compile(
    r"\b(?:candidate|candidates|applicant|applicants|people|person|who|whom)\b",
    re.IGNORECASE,
)
_EXPLICIT_HISTORY_RE = re.compile(
    r"\b(?:candidate|application)\s+(?:action|actions|movement|movements|history)\b",
    re.IGNORECASE,
)
_ACTOR_COMPLETED_ACTION_RE = re.compile(
    r"\b(?:i|we|you|the\s+agent|agent)\s+(?:have\s+|has\s+|had\s+)?"
    r"(?:advanced|rejected|hired|withdrew|moved|sent|resent|invited)\b",
    re.IGNORECASE,
)
_DECISION_HISTORY_RE = re.compile(
    r"\b(?:agent\s+)?(?:decision|decisions|recommendation|recommendations)\b|"
    r"\b(?:recommend|recommended|recommends|overrode|overridden|override)\b",
    re.IGNORECASE,
)
_POOL_REQUEST_RE = re.compile(
    r"\b(?:list|show|find|search|compare|rank|count|how\s+many|which|who|"
    r"are\s+there|do\s+(?:we|i|you)\s+have|should\s+(?:we|i))\b",
    re.IGNORECASE,
)
_POOL_ASSERTION_RE = re.compile(
    r"(?:\b(?:zero|no|none|any|all|every|entire|whole|exact|exhaustive|"
    r"exhaustively|hard\s+zero)\b.{0,80}\b(?:candidate|candidates|applicant|"
    r"applicants|pool)\b)|(?:\b(?:candidate|candidates|applicant|applicants|"
    r"pool)\b.{0,80}\b(?:zero|none|all|every|exact|exhaustive|empty)\b)",
    re.IGNORECASE,
)
_CURRENT_STATE_RE = re.compile(
    r"\b(?:current|currently|pool|pipeline|stage|status|score|fit|experience|"
    r"skill|skills|qualified|available|strongest|best|top|advance|advanced|"
    r"reject|rejected|withdrawn|hired|assessment|interview)\b",
    re.IGNORECASE,
)
_FUTURE_CANDIDATE_ACTION_RE = re.compile(
    r"\bshould\s+(?:i|we)\s+(?:advance|reject|hire|interview|assess)\b",
    re.IGNORECASE,
)


def required_capabilities_for_message(message: str | None) -> frozenset[str]:
    """Return canonical reads a terminal answer must have used this turn."""

    text = str(message or "").strip()
    if not text:
        return frozenset()
    candidate_context = bool(
        _CANDIDATE_CONTEXT_RE.search(text)
        or _FUTURE_CANDIDATE_ACTION_RE.search(text)
    )
    requires_action_history = bool(_EXPLICIT_HISTORY_RE.search(text)) or bool(
        candidate_context
        and (
            _ACTOR_COMPLETED_ACTION_RE.search(text)
            or (
                _COMPLETED_ACTION_RE.search(text)
                and _HISTORY_MARKER_RE.search(text)
            )
        )
    )
    required: set[str] = set()
    if requires_action_history:
        required.add(CANDIDATE_ACTION_HISTORY)

    requires_decision_history = bool(_DECISION_HISTORY_RE.search(text))
    if requires_decision_history:
        required.add(CANDIDATE_DECISION_HISTORY)

    # Current pool/state questions and exact/empty assertions require a
    # canonical role-scoped read. Historical action/decision tools already
    # return candidate identity and their own exact totals, so do not require a
    # redundant pool read unless the message independently asks about current
    # state too.
    asks_for_pool = bool(
        candidate_context
        and (
            _POOL_ASSERTION_RE.search(text)
            or (
                _POOL_REQUEST_RE.search(text)
                and _CURRENT_STATE_RE.search(text)
            )
        )
    )
    if asks_for_pool and not (requires_action_history or requires_decision_history):
        required.add(CANDIDATE_POOL_STATE)
    return frozenset(required)


def grounding_required_message(missing: Iterable[str]) -> str:
    """Return a capability-specific fail-closed response."""

    capabilities = frozenset(missing)
    if capabilities == {CANDIDATE_ACTION_HISTORY}:
        return ACTION_HISTORY_REQUIRED_MESSAGE
    if capabilities == {CANDIDATE_DECISION_HISTORY}:
        return DECISION_HISTORY_REQUIRED_MESSAGE
    if capabilities == {CANDIDATE_POOL_STATE}:
        return POOL_STATE_REQUIRED_MESSAGE
    return GROUNDING_REQUIRED_MESSAGE


def latest_user_text(messages: Iterable[dict[str, Any]]) -> str:
    """Extract the newest ordinary user text from an Anthropic transcript."""

    for message in reversed(list(messages)):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if content.strip():
                return content.strip()
            continue
        if not isinstance(content, list):
            continue
        chunks = [
            str(block.get("text") or "").strip()
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(chunk for chunk in chunks if chunk).strip()
        if text:
            return text
    return ""


def missing_required_capabilities(
    required: Iterable[str],
    grounded: Iterable[str],
) -> frozenset[str]:
    return frozenset(required).difference(grounded)


__all__ = [
    "ACTION_HISTORY_REQUIRED_MESSAGE",
    "DECISION_HISTORY_REQUIRED_MESSAGE",
    "GROUNDING_REQUIRED_MESSAGE",
    "POOL_STATE_REQUIRED_MESSAGE",
    "grounding_required_message",
    "latest_user_text",
    "missing_required_capabilities",
    "required_capabilities_for_message",
]
