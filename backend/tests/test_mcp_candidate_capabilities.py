"""Transport-independent contracts for grounded candidate reads."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.mcp.catalog import (
    AGENT_CHAT,
    AUTONOMOUS_AGENT,
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_POOL_STATE,
)
from app.mcp.handlers import _candidate_action_from_event
from app.mcp.provenance import required_capabilities_for_message
from app.mcp.shared_reads import (
    capabilities_for_successful_read,
    dispatch_shared_read,
    shared_read_definitions,
)


@pytest.mark.parametrize("exposure", [AGENT_CHAT, AUTONOMOUS_AGENT])
def test_role_bound_transports_hide_model_supplied_role_identity(exposure):
    definitions = {
        item["name"]: item for item in shared_read_definitions(exposure, bound_role=True)
    }

    for name in (
        "search_role_candidates",
        "get_role_candidate",
        "list_candidate_actions",
        "list_recent_agent_decisions",
        "get_recruiting_overview",
    ):
        assert "role_id" not in definitions[name]["input_schema"]["properties"]


def test_bound_dispatch_injects_role_and_rejects_scope_spoofing():
    db = object()
    principal = SimpleNamespace(organization_id=7)
    with patch("app.mcp.handlers.search_role_candidates") as handler:
        handler.return_value = {"items": [], "total": 0, "total_is_exact": True}
        result = dispatch_shared_read(
            "search_role_candidates",
            {},
            exposure=AGENT_CHAT,
            db=db,
            principal=principal,
            bound_role_id=42,
        )
        assert result["total"] == 0
        handler.assert_called_once_with(db, principal, role_id=42)

        with pytest.raises(ValueError, match="bound to the active role"):
            dispatch_shared_read(
                "search_role_candidates",
                {"role_id": 99},
                exposure=AGENT_CHAT,
                db=db,
                principal=principal,
                bound_role_id=42,
            )


@pytest.mark.parametrize(
    "message",
    [
        "Give me the candidates I advanced to technical interview last week",
        "Who did we reject yesterday?",
        "When were these applicants sent an assessment?",
        "Show the candidate action history",
        "Show me the candidates I advanced",
        "List the people we sent assessments to",
    ],
)
def test_completed_action_questions_require_action_history(message):
    assert required_capabilities_for_message(message) == frozenset(
        {CANDIDATE_ACTION_HISTORY}
    )


@pytest.mark.parametrize(
    "message",
    [
        "Who is currently in technical interview?",
        "Should I advance Ada?",
        "Show candidates with banking experience",
        "List rejected candidates",
    ],
)
def test_current_state_or_future_action_questions_require_pool_state(message):
    assert required_capabilities_for_message(message) == frozenset(
        {CANDIDATE_POOL_STATE}
    )


@pytest.mark.parametrize(
    "message",
    [
        "Show the pending agent decisions",
        "Which candidates did the agent recommend?",
        "List overridden recommendations from last week",
    ],
)
def test_recommendation_questions_require_decision_history(message):
    assert required_capabilities_for_message(message) == frozenset(
        {CANDIDATE_DECISION_HISTORY}
    )


def test_unprompted_hard_zero_claim_requires_pool_state():
    assert required_capabilities_for_message(
        "Zero candidates have PySpark experience in this pool."
    ) == frozenset({CANDIDATE_POOL_STATE})


def test_non_candidate_chat_does_not_require_candidate_grounding():
    assert required_capabilities_for_message("Hello, can you help me?") == frozenset()


def test_inexact_action_read_cannot_ground_an_exhaustive_history_answer():
    assert capabilities_for_successful_read(
        "list_candidate_actions",
        {"items": [], "total": 0, "total_is_exact": False},
    ) == frozenset()
    assert capabilities_for_successful_read(
        "list_candidate_actions",
        {"items": [], "total": 0, "total_is_exact": True},
    ) == frozenset({CANDIDATE_ACTION_HISTORY})


def test_inexact_pool_or_decision_reads_cannot_ground_exhaustive_claims():
    assert capabilities_for_successful_read(
        "search_role_candidates",
        {"items": [], "total": 0, "total_is_exact": False},
    ) == frozenset()
    assert capabilities_for_successful_read(
        "search_role_candidates",
        {"items": [], "total": 0, "total_is_exact": True},
    ) == frozenset({CANDIDATE_POOL_STATE})
    assert capabilities_for_successful_read(
        "list_recent_agent_decisions",
        {"items": [], "total": 0, "total_is_exact": False},
    ) == frozenset()
    assert capabilities_for_successful_read(
        "list_recent_agent_decisions",
        {"items": [], "total": 0, "total_is_exact": True},
    ) == frozenset({CANDIDATE_DECISION_HISTORY})


def test_qualitative_search_only_grounds_positive_or_exact_empty_results():
    assert capabilities_for_successful_read(
        "find_top_candidates",
        {"candidates": [{"application_id": 1}], "is_exact_empty": False},
    ) == frozenset({CANDIDATE_POOL_STATE})
    assert capabilities_for_successful_read(
        "find_top_candidates",
        {"candidates": [], "is_exact_empty": True, "exhaustive": True},
    ) == frozenset({CANDIDATE_POOL_STATE})
    assert capabilities_for_successful_read(
        "find_top_candidates",
        {"candidates": [], "is_exact_empty": False, "exhaustive": False},
    ) == frozenset()


@pytest.mark.parametrize(
    ("metadata", "expected_action"),
    [
        ({"source": "reject_application", "action": "disqualify"}, "rejected"),
        ({"source": "decision_summary", "action": "move"}, "advanced"),
        ({"op_type": "move_stage", "target_stage": "Technical Interview"}, "advanced"),
        ({"source": "post_note"}, None),
    ],
)
def test_generic_provider_failures_are_classified_from_recorded_operation(
    metadata, expected_action
):
    event = SimpleNamespace(
        event_type="workable_writeback_failed",
        event_metadata=metadata,
        effect_status="failed",
        target_stage=metadata.get("target_stage"),
        agent_decision_id=None,
        from_stage=None,
        to_stage=None,
        from_outcome=None,
        to_outcome=None,
    )

    classified = _candidate_action_from_event(event)
    assert (classified or {}).get("action") == expected_action
