"""Deterministic carry-forward for a chat search's active occupation scope.

The model still receives the full conversation, but a terse refinement must not
silently turn "project managers with Treasury experience" into an org-wide
search merely because its tool query says only "Treasury banking experience".
This module recovers the most recent known-title population from recruiter text
or a prior search tool call. It deliberately carries only occupations; merging
arbitrary narrative requirements server-side would make explicit replacements
and relaxations ambiguous.
"""

from __future__ import annotations

import re
from typing import Any

from ..candidate_search.deterministic_parser import parse_common_query
from ..candidate_search.skill_aliases import COMMON_TITLES

_SEARCH_TOOLS = frozenset({"find_top_candidates", "screen_pool_against_requirement"})
_RESET_POPULATION_RE = re.compile(
    r"\b(?:any\s+(?:role|title)|all\s+(?:roles|titles)|across\s+(?:all\s+)?roles|"
    r"regardless\s+of\s+(?:role|title)|no\s+(?:role|title)\s+restriction|"
    r"(?:drop|remove|clear)\s+(?:the\s+)?(?:role|title))\b",
    re.IGNORECASE,
)
_TITLE_EXCLUSION_PREFIX_RE = re.compile(
    r"(?:\bnot(?:\s+looking\s+for)?|\bexclude(?:d|s|ing)?|\bwithout|"
    r"\bexcept(?:\s+for)?|\bother\s+than|\banything\s+but|"
    r"\b(?:do\s+not|don['’]?t)\s+(?:want|include|show|find))"
    r"\s+(?:any\s+|the\s+|an?\s+)?$",
    re.IGNORECASE,
)
_TITLE_EXCLUSION_LEAD_RE = re.compile(
    r"(?:\bnot(?:\s+looking\s+for)?|\bexclude(?:d|s|ing)?|\bwithout|"
    r"\bexcept(?:\s+for)?|\bother\s+than|\banything\s+but|"
    r"\b(?:do\s+not|don['’]?t)\s+(?:want|include|show|find))"
    r"\s+(?:any\s+|the\s+|an?\s+)?",
    re.IGNORECASE,
)


def _title_is_excluded(text: str, start: int) -> bool:
    """Whether the words immediately before a title explicitly reject it."""

    prefix = text[max(0, start - 160) : start]
    if _TITLE_EXCLUSION_PREFIX_RE.search(prefix):
        return True

    # Carry an exclusion across a coordinated title list: in "not project
    # managers or scrum masters", the second title is preceded only by another
    # known title plus a connector. Stop carrying as soon as narrative words
    # such as "but find" introduce a positive replacement.
    for lead in _TITLE_EXCLUSION_LEAD_RE.finditer(prefix):
        between = prefix[lead.end() :]
        for title in sorted(COMMON_TITLES, key=len, reverse=True):
            between = re.sub(
                rf"(?<![a-z0-9]){re.escape(title)}s?(?![a-z0-9])",
                " ",
                between,
                flags=re.IGNORECASE,
            )
        between = re.sub(
            r"\b(?:and|or|nor|as\s+well\s+as)\b|[,/&]",
            " ",
            between,
            flags=re.IGNORECASE,
        )
        if not between.strip():
            return True
    return False


def _has_excluded_known_title(text: str) -> bool:
    clean = str(text or "")
    for title in COMMON_TITLES:
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(title)}s?(?![a-z0-9])",
            clean,
            re.IGNORECASE,
        ):
            if _title_is_excluded(clean, match.start()):
                return True
    return False


def _known_title_context(text: str) -> dict[str, list[str]] | None:
    clean = str(text or "").strip()
    if not clean:
        return None
    # Negated title mentions must be interpreted by the occurrence-aware scan
    # below; the normal parser represents positive filters only.
    parsed = None if _has_excluded_known_title(clean) else parse_common_query(clean)
    if parsed is not None and (parsed.titles_all or parsed.titles_any):
        return {
            "titles_all": list(parsed.titles_all),
            "titles_any": list(parsed.titles_any),
        }

    matches: list[tuple[int, int, str]] = []
    for title in sorted(COMMON_TITLES, key=len, reverse=True):
        match = next(
            (
                candidate
                for candidate in re.finditer(
                    rf"(?<![a-z0-9]){re.escape(title)}s?(?![a-z0-9])",
                    clean,
                    re.IGNORECASE,
                )
                if not _title_is_excluded(clean, candidate.start())
            ),
            None,
        )
        if match is None:
            continue
        if any(match.start() < end and match.end() > start for start, end, _ in matches):
            continue
        matches.append((match.start(), match.end(), title))
    if not matches:
        return None
    titles = [title for _start, _end, title in sorted(matches)]
    return {
        "titles_all": [] if re.search(r"\s+or\s+", clean, re.IGNORECASE) else titles,
        "titles_any": titles if re.search(r"\s+or\s+", clean, re.IGNORECASE) else [],
    }


def _latest_recruiter_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        for block in reversed(message.get("content") or []):
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "")
    return ""


def population_context_for_search(
    messages: list[dict[str, Any]], *, current_query: str
) -> dict[str, list[str]] | None:
    """Return the latest active known-title population for a search tool call."""

    latest_user = _latest_recruiter_text(messages)
    if _RESET_POPULATION_RE.search(current_query or "") or _RESET_POPULATION_RE.search(
        latest_user
    ):
        return None

    # A title-specific exclusion supersedes older chat state. If the same
    # message names a different positive occupation, use that replacement;
    # otherwise clear the inherited population instead of resurrecting it from
    # an earlier turn.
    for recent_text in (str(current_query or ""), latest_user):
        if _has_excluded_known_title(recent_text):
            return _known_title_context(recent_text)

    candidates: list[str] = [str(current_query or "")]
    for message in reversed(messages):
        role = message.get("role")
        for block in reversed(message.get("content") or []):
            if not isinstance(block, dict):
                continue
            if role == "assistant" and block.get("type") == "tool_use":
                if str(block.get("name") or "") in _SEARCH_TOOLS:
                    candidates.append(str((block.get("input") or {}).get("query") or ""))
            elif role == "user" and block.get("type") == "text":
                candidates.append(str(block.get("text") or ""))

    for text in candidates:
        context = _known_title_context(text)
        if context is not None:
            return context
    return None


__all__ = ["population_context_for_search"]
