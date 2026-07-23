from __future__ import annotations

import json

import pytest

from app.candidate_search.tool_failure_contract import (
    CANDIDATE_SEARCH_TOOL_NAMES,
    CANDIDATE_SEARCH_UNAVAILABLE_CODE,
    TERMINAL_UNVERIFIED_SEARCH_STATUSES,
    candidate_search_failure_result,
    candidate_search_result_failed,
    candidate_search_tools_first,
    is_candidate_search_tool,
)


@pytest.mark.parametrize("tool_name", sorted(CANDIDATE_SEARCH_TOOL_NAMES))
def test_every_candidate_search_alias_uses_the_failure_contract(tool_name):
    assert is_candidate_search_tool(tool_name)


@pytest.mark.parametrize(
    "search_status",
    sorted(TERMINAL_UNVERIFIED_SEARCH_STATUSES),
)
def test_every_unverified_search_status_is_terminal(search_status):
    assert candidate_search_result_failed(
        "find_top_candidates",
        {"search_status": search_status, "candidates": []},
    )


@pytest.mark.parametrize("result", [{"available": False}, {"error": "database failed"}])
def test_error_or_unavailable_search_result_is_terminal(result):
    assert candidate_search_result_failed("find_top_candidates", result)


def test_non_mapping_search_result_is_terminal():
    assert candidate_search_result_failed("find_top_candidates", [])


def test_structured_application_search_keeps_its_legacy_list_contract():
    assert not candidate_search_result_failed("search_applications", [])


def test_canonical_role_search_fails_closed_when_current_state_is_unavailable():
    assert is_candidate_search_tool("search_role_candidates")
    assert candidate_search_result_failed(
        "search_role_candidates",
        {
            "available": False,
            "error": "role projection unavailable",
            "applications": [],
            "total_is_exact": False,
        },
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "search_role_candidates",
        "get_role_candidate",
        "list_candidate_actions",
        "list_recent_agent_decisions",
    ],
)
def test_every_canonical_candidate_fact_read_is_terminal_on_failure(tool_name):
    assert is_candidate_search_tool(tool_name)
    assert candidate_search_result_failed(
        tool_name,
        {"available": False, "error": "authoritative read unavailable"},
    )


def test_terminal_warning_code_is_classified_even_without_top_level_status():
    assert candidate_search_result_failed(
        "nl_search_candidates",
        {
            "warnings": [
                {"code": "rerank_skipped", "message": "raw provider marker"}
            ],
            "applications": [],
        },
    )


@pytest.mark.parametrize(
    "warning_code",
    ["evidence_incomplete", "rerank_partial", "verification_capped"],
)
def test_zero_success_verification_warning_is_terminal(warning_code):
    assert candidate_search_result_failed(
        "find_top_candidates",
        {
            "warnings": [{"code": warning_code}],
            "candidates": [],
            "returned": 0,
            "evidence_succeeded": 0,
            "is_exact_empty": False,
        },
    )


@pytest.mark.parametrize(
    "warning_code",
    [
        "graph_coverage_partial",
        "graph_retrieval_failed",
        "graph_retrieval_unavailable",
    ],
)
def test_empty_inexact_graph_degradation_is_terminal(warning_code):
    assert candidate_search_result_failed(
        "nl_search_candidates",
        {
            "warnings": [{"code": warning_code}],
            "applications": [],
            "returned": 0,
            "is_exact_empty": False,
        },
    )


@pytest.mark.parametrize(
    "search_status", ["no_verified_matches", "no_actionable_candidates"]
)
@pytest.mark.parametrize(
    "tool_name", ["find_top_candidates", "screen_pool_against_requirement"]
)
def test_zero_of_zero_qualitative_checks_cannot_be_a_verified_empty_result(
    search_status, tool_name
):
    """Regression for the false PySpark "checked everyone" production claim."""

    assert candidate_search_result_failed(
        tool_name,
        {
            "search_status": search_status,
            "pool_size": 0,
            "returned": 0,
            "deep_checked": 0,
            "evidence_succeeded": 0,
            "criteria_requested": ["AI engineers with PySpark experience"],
            "required_criteria": ["AI engineers with PySpark experience"],
            "is_exact_empty": True,
            "exhaustive": True,
            "candidates": [],
        },
    )


def test_narrowed_structural_zero_is_terminal_before_model_narration():
    """A zero over an actionable slice cannot be narrated as a roster zero."""

    assert candidate_search_result_failed(
        "find_top_candidates",
        {
            "search_status": "structural_retrieval_incomplete",
            "pool_size": 2,
            "role_roster_size": 5,
            "returned": 0,
            "structural_matches": 0,
            "qualified_total": None,
            "is_exact_empty": False,
            "exhaustive": False,
            "candidates": [],
            "warnings": [
                {
                    "code": "structural_retrieval_incomplete",
                    "message": "The actionable subset did not cover the roster.",
                }
            ],
        },
    )


@pytest.mark.parametrize(
    "result",
    [
        {"is_exact_empty": True, "candidates": []},
        {
            "search_status": "no_structural_matches",
            "is_exact_empty": True,
            "candidates": [],
        },
        {"search_status": "complete", "candidates": [{"application_id": 1}]},
        {"warnings": [{"code": "graph_partial"}], "applications": []},
        {
            "warnings": [{"code": "evidence_incomplete"}],
            "candidates": [{"application_id": 1}],
            "returned": 1,
            "evidence_succeeded": 1,
            "is_exact_empty": False,
        },
        {
            "warnings": [{"code": "graph_retrieval_unavailable"}],
            "applications": [{"application_id": 1}],
            "returned": 1,
            "is_exact_empty": False,
        },
        {
            "warnings": [{"code": "verification_capped"}],
            "applications": [],
            "returned": 0,
            "evidence_succeeded": 0,
            "is_exact_empty": True,
        },
    ],
)
def test_truthful_search_outcomes_are_not_misclassified_as_failures(result):
    assert not candidate_search_result_failed("find_top_candidates", result)


def test_search_tools_are_stably_prioritized_before_mutations():
    blocks = [
        {"type": "text", "text": "planning"},
        {"type": "tool_use", "id": "mutate", "name": "set_threshold"},
        {"type": "tool_use", "id": "search-1", "name": "find_top_candidates"},
        {"type": "tool_use", "id": "read", "name": "get_role"},
        {"type": "tool_use", "id": "search-2", "name": "nl_search_candidates"},
    ]

    ordered = candidate_search_tools_first(blocks)

    assert [block["id"] for block in ordered] == [
        "search-1",
        "search-2",
        "mutate",
        "read",
    ]


def test_safe_failure_envelope_cannot_serialize_raw_exception_details():
    raw_marker = "SELECT secret FROM candidates"

    payload = candidate_search_failure_result(
        tool="find_top_candidates",
        incident_id="incident-123",
    )
    serialized = json.dumps(payload)

    assert payload["code"] == CANDIDATE_SEARCH_UNAVAILABLE_CODE
    assert payload["search_completed"] is False
    assert payload["is_exact_empty"] is None
    assert raw_marker not in serialized
