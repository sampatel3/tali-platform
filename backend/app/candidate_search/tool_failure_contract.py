"""Server-owned failure semantics for candidate-search tools.

Search failures are terminal for the current agent turn: a model must never
reinterpret a database/provider exception as evidence about the candidate pool.
This module deliberately accepts only tool names/results, never exceptions, so
raw failure details cannot leak into a transcript or a second model call.
"""

from __future__ import annotations

import uuid
from typing import Any


CANDIDATE_SEARCH_UNAVAILABLE_CODE = "candidate_search_unavailable"
CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE = (
    "I couldn't complete a verified candidate search, so I haven't treated this "
    "as zero matches or produced a shortlist. Please try again."
)
CANDIDATE_SEARCH_TOOL_NAMES = frozenset(
    {
        "search_candidates",
        "search_applications",
        "nl_search_candidates",
        "find_top_candidates",
        "screen_pool_against_requirement",
        "graph_search_candidates",
    }
)
TERMINAL_UNVERIFIED_SEARCH_STATUSES = frozenset(
    {
        "parser_failed",
        "required_criteria_unchecked",
        "search_plan_failed",
        "structural_retrieval_incomplete",
        "unsupported_search_constraint",
        "verification_unavailable",
        "rerank_skipped",
    }
)
CONDITIONAL_VERIFICATION_FAILURE_WARNINGS = frozenset(
    {"evidence_incomplete", "rerank_partial", "verification_capped"}
)
CONDITIONAL_EMPTY_RETRIEVAL_WARNINGS = frozenset(
    {
        "graph_coverage_partial",
        "graph_retrieval_failed",
        "graph_retrieval_unavailable",
    }
)


def is_candidate_search_tool(name: str) -> bool:
    return str(name or "") in CANDIDATE_SEARCH_TOOL_NAMES


def candidate_search_tools_first(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stable-partition tool calls so search failure precedes side effects."""

    calls = [block for block in blocks if block.get("type") == "tool_use"]
    searches = [block for block in calls if is_candidate_search_tool(block.get("name", ""))]
    others = [block for block in calls if not is_candidate_search_tool(block.get("name", ""))]
    return searches + others


def candidate_search_result_failed(name: str, result: Any) -> bool:
    """Classify only terminal/unverified outcomes; exact-empty remains valid."""

    if not is_candidate_search_tool(name):
        return False
    if not isinstance(result, dict):
        # The legacy structured SQL search deliberately returns a list. Every
        # semantic/grounded search returns a typed mapping.
        return not (name == "search_applications" and isinstance(result, list))
    warning_codes = {
        str(
            warning.get("code")
            if isinstance(warning, dict)
            else getattr(warning, "code", "")
        )
        for warning in (result.get("warnings") or [])
    }
    evidence_zero = (
        "evidence_succeeded" in result
        and result.get("evidence_succeeded") == 0
        and result.get("is_exact_empty") is not True
    )
    explicit_empty = result.get("returned") == 0 or any(
        key in result and isinstance(result.get(key), list) and not result.get(key)
        for key in ("applications", "candidates")
    )
    incomplete_empty_retrieval = bool(
        explicit_empty
        and result.get("is_exact_empty") is False
        and warning_codes.intersection(CONDITIONAL_EMPTY_RETRIEVAL_WARNINGS)
    )
    # A qualitative "no verified matches" verdict is impossible when no
    # candidate evidence check ran.  In particular, ``is_exact_empty`` may
    # describe an empty/mis-scoped retrieval query; it cannot turn 0/0 CV
    # checks into a grounded negative.  Stop the tool round server-side so the
    # model never receives that payload as permission to say "checked everyone".
    unchecked_qualitative_zero = bool(
        name in {"find_top_candidates", "screen_pool_against_requirement"}
        and result.get("search_status")
        in {"no_verified_matches", "no_actionable_candidates"}
        and result.get("deep_checked") == 0
        and result.get("evidence_succeeded") == 0
        and (result.get("criteria_requested") or result.get("required_criteria"))
    )
    return bool(
        result.get("error")
        or result.get("available") is False
        or result.get("search_status") in TERMINAL_UNVERIFIED_SEARCH_STATUSES
        or warning_codes.intersection(TERMINAL_UNVERIFIED_SEARCH_STATUSES)
        or (
            evidence_zero
            and warning_codes.intersection(
                CONDITIONAL_VERIFICATION_FAILURE_WARNINGS
            )
        )
        or incomplete_empty_retrieval
        or unchecked_qualitative_zero
    )


def new_candidate_search_incident_id() -> str:
    return uuid.uuid4().hex[:12]


def candidate_search_failure_result(
    *, tool: str, incident_id: str
) -> dict[str, Any]:
    return {
        "code": CANDIDATE_SEARCH_UNAVAILABLE_CODE,
        "error": CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE,
        "tool": str(tool),
        "retryable": True,
        "search_completed": False,
        "is_exact_empty": None,
        "incident_id": str(incident_id),
    }


def skipped_after_search_failure_result(
    *, tool: str, incident_id: str
) -> dict[str, Any]:
    return {
        "code": "not_executed_after_search_failure",
        "error": "Not executed because the required candidate search did not complete.",
        "tool": str(tool),
        "retryable": True,
        "incident_id": str(incident_id),
    }


def unexpected_tool_failure_result(
    *, tool: str, incident_id: str
) -> dict[str, Any]:
    """Sanitized envelope for unexpected non-search tool exceptions."""

    return {
        "code": "tool_execution_failed",
        "error": "The tool could not be completed. Please try again.",
        "tool": str(tool),
        "retryable": True,
        "incident_id": str(incident_id),
    }


__all__ = [
    "CANDIDATE_SEARCH_TOOL_NAMES",
    "CANDIDATE_SEARCH_UNAVAILABLE_CODE",
    "CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE",
    "CONDITIONAL_EMPTY_RETRIEVAL_WARNINGS",
    "CONDITIONAL_VERIFICATION_FAILURE_WARNINGS",
    "TERMINAL_UNVERIFIED_SEARCH_STATUSES",
    "candidate_search_failure_result",
    "candidate_search_result_failed",
    "candidate_search_tools_first",
    "is_candidate_search_tool",
    "new_candidate_search_incident_id",
    "skipped_after_search_failure_result",
    "unexpected_tool_failure_result",
]
