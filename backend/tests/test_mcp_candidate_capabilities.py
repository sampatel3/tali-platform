"""Transport-independent contracts for grounded candidate reads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.mcp.catalog import (
    AGENT_CHAT,
    AUTONOMOUS_AGENT,
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
    CANDIDATE_POOL_EXHAUSTIVE,
    CANDIDATE_POOL_STATE,
    CANDIDATE_QUALITATIVE_EVIDENCE,
    CANDIDATE_QUALITATIVE_EXACT_EMPTY,
)
from app.domains.assessments_runtime.pipeline_event_service import (
    resolve_historical_event_role_id,
)
from app.mcp.handlers import (
    _candidate_action_from_event,
    _canonical_action_application_id,
)
from app.mcp.provenance import (
    GroundingClaim,
    grounding_claims_for_message,
    meaningful_qualitative_terms,
    required_capabilities_for_message,
)
from app.mcp.required_reads import RequiredReadController
from app.mcp.shared_reads import (
    GroundingLedger,
    capabilities_for_successful_read,
    dispatch_shared_read,
    shared_read_definitions,
)


@pytest.mark.parametrize("exposure", [AGENT_CHAT, AUTONOMOUS_AGENT])
def test_role_bound_transports_hide_model_supplied_role_identity(exposure):
    definitions = {
        item["name"]: item
        for item in shared_read_definitions(exposure, bound_role=True)
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
        {CANDIDATE_ACTION_HISTORY_EXHAUSTIVE}
    )


def test_exact_demo_action_query_is_not_misclassified_as_qualitative():
    now = datetime(2026, 7, 22, 15, 13, tzinfo=timezone.utc)
    message = (
        "can you give me a list of candidates that i have advanced to "
        "technical interview in the last week"
    )

    claims = grounding_claims_for_message(message, now=now)

    assert [claim.capability for claim in claims] == [
        CANDIDATE_ACTION_HISTORY_EXHAUSTIVE
    ]
    assert claims[0].filter_map == {
        "action": "advanced",
        "actor_id": "current_user",
        "actor_type": "recruiter",
        "target_stage": "technical interview",
        "time_after": (now - timedelta(days=7)).isoformat(),
        "time_before": now.isoformat(),
    }
    controller = RequiredReadController(
        GroundingLedger(message, now=now),
        current_user_id=77,
    )
    plan = controller.next_plan()
    assert plan is not None
    assert plan.tool_name == "list_candidate_actions"
    assert plan.arguments == {
        "status": "confirmed",
        "result_view": "candidates",
        "actor_id": 77,
        "actor_type": "recruiter",
        "limit": 100,
        "offset": 0,
        "action": "advanced",
        "target_stage": "technical interview",
        "occurred_after": (now - timedelta(days=7)).isoformat(),
        "occurred_before": now.isoformat(),
    }
    assert controller.next_plan() is None


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("Show PySpark candidates", "find_top_candidates"),
        ("Who is currently in technical interview?", "search_role_candidates"),
        ("Show the pending agent decisions", "list_recent_agent_decisions"),
    ],
)
def test_required_read_controller_maps_candidate_claims_to_canonical_tools(
    message, expected_tool
):
    controller = RequiredReadController(GroundingLedger(message))

    plan = controller.next_plan()

    assert plan is not None
    assert plan.tool_choice == {
        "type": "tool",
        "name": expected_tool,
        "disable_parallel_tool_use": True,
    }
    assert controller.next_plan() is None


@pytest.mark.parametrize(
    (
        "message",
        "expected_capability",
        "expected_tool",
        "expected_filters",
        "wrong_subject_answer",
    ),
    [
        (
            "Did I advance Sam?",
            CANDIDATE_ACTION_HISTORY,
            "list_candidate_actions",
            {
                "action": "advanced",
                "actor_id": 77,
                "actor_type": "recruiter",
                "candidate_id": 41,
                "status": "confirmed",
            },
            "Did I advance Jordan? Yes.",
        ),
        (
            "What did the agent decide for Sam?",
            CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
            "list_recent_agent_decisions",
            {"candidate_id": 41},
            "What did the agent decide for Jordan?",
        ),
    ],
)
def test_named_history_questions_resolve_role_local_candidate_before_history_read(
    message,
    expected_capability,
    expected_tool,
    expected_filters,
    wrong_subject_answer,
):
    ledger = GroundingLedger(message)
    controller = RequiredReadController(ledger, current_user_id=77)

    resolution_plan = controller.next_plan()

    assert resolution_plan is not None
    assert resolution_plan.tool_name == "search_role_candidates"
    assert resolution_plan.arguments == {
        "q": "sam",
        "application_outcome": None,
        "limit": 100,
        "offset": 0,
    }
    resolution_result = _exact_page(
        items=[
            {
                "application_id": 9,
                "candidate_id": 41,
                "candidate_name": "Sam Patel",
            }
        ],
        total=1,
        filters={"q": "sam", "application_outcome": None},
    )
    ledger.observe(
        resolution_plan.tool_name,
        resolution_result,
        arguments=resolution_plan.arguments,
    )
    controller.observe(
        resolution_plan,
        tool_name=resolution_plan.tool_name,
        result=resolution_result,
        arguments=resolution_plan.arguments,
    )

    history_plan = controller.next_plan()

    assert history_plan is not None
    assert history_plan.tool_name == expected_tool
    for key, value in expected_filters.items():
        assert history_plan.arguments[key] == value
    assert ledger.required_claims == (
        GroundingClaim(
            capability=expected_capability,
            filters=tuple(
                sorted(
                    (key, str(value))
                    for key, value in expected_filters.items()
                    if key != "status"
                )
            ),
        ),
    )

    history_result = _exact_page(
        items=[],
        total=0,
        filters={
            key: value
            for key, value in history_plan.arguments.items()
            if key not in {"limit", "offset"}
        },
    )
    ledger.observe(
        history_plan.tool_name,
        history_result,
        arguments=history_plan.arguments,
    )

    assert not ledger.missing_for_answer("No.")
    assert expected_capability in ledger.missing_for_answer(wrong_subject_answer)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [
            {
                "application_id": 9,
                "candidate_id": 41,
                "candidate_name": "Sam Patel",
            },
            {
                "application_id": 10,
                "candidate_id": 42,
                "candidate_name": "Sam Reed",
            },
        ],
    ],
)
def test_named_history_resolution_fails_closed_for_ambiguous_or_missing_candidates(
    items,
):
    ledger = GroundingLedger("What did the agent decide for Sam?")
    controller = RequiredReadController(ledger)
    resolution_plan = controller.next_plan()
    assert resolution_plan is not None
    result = _exact_page(
        items=items,
        total=len(items),
        filters={"q": "sam", "application_outcome": None},
    )
    ledger.observe(
        resolution_plan.tool_name,
        result,
        arguments=resolution_plan.arguments,
    )
    controller.observe(
        resolution_plan,
        tool_name=resolution_plan.tool_name,
        result=result,
        arguments=resolution_plan.arguments,
    )

    assert controller.next_plan() is None
    assert ledger.required_claims[0].subjects == ("sam",)
    assert ledger.required_claims[0].subject_resolution_required is True
    ledger.observe(
        "list_recent_agent_decisions",
        _exact_page(
            items=[{"id": 77, "candidate_name": "Sam"}],
            total=1,
            filters={"candidate_id": 41},
        ),
        arguments={"candidate_id": 41},
    )
    assert CANDIDATE_DECISION_HISTORY_EXHAUSTIVE in ledger.missing_for_answer("No.")


@pytest.mark.parametrize(
    "message",
    [
        "Did I advance Sam?",
        "What did the agent decide for Sam?",
    ],
)
def test_named_history_requires_one_role_before_candidate_resolution(message):
    controller = RequiredReadController(
        GroundingLedger(message),
        role_bound=False,
        current_user_id=77,
    )

    assert controller.requires_role_scope is True
    assert controller.next_plan() is None


def test_request_history_subjects_are_preserved_without_preordaining_answer_values():
    request = "Did I advance Sam? I think 2 actions were confirmed."
    [parsed_claim] = grounding_claims_for_message(request)
    [required_claim] = GroundingLedger(request).required_claims

    assert parsed_claim.subjects == ("sam",)
    assert parsed_claim.expected_total == 2
    assert required_claim.subjects == ("sam",)
    assert required_claim.expected_total is None
    assert required_claim.subject_resolution_required is True


def test_required_read_controller_does_not_continue_a_wrong_scope():
    now = datetime(2026, 7, 22, 15, 13, tzinfo=timezone.utc)
    request = "Give me the candidates I advanced to technical interview last week"
    ledger = GroundingLedger(request, now=now)
    controller = RequiredReadController(ledger, current_user_id=77)
    plan = controller.next_plan()
    assert plan is not None
    filters = {
        "action": "advanced",
        "target_stage": "Final Interview",
        "status": "confirmed",
        "actor_id": 77,
        "actor_type": "recruiter",
        "occurred_after": (now - timedelta(days=7)).isoformat(),
        "occurred_before": now.isoformat(),
        "result_view": "candidates",
    }
    result = _exact_page(
        items=[{"event_id": 1, "candidate_name": "Avery Stone"}],
        total=1,
        filters=filters,
    )
    ledger.observe("list_candidate_actions", result, arguments=filters)
    controller.observe(
        plan,
        tool_name="list_candidate_actions",
        result=result,
        arguments=filters,
    )

    assert CANDIDATE_ACTION_HISTORY_EXHAUSTIVE in ledger.missing_for_answer("")
    assert controller.next_plan() is None


def test_required_read_controller_continues_valid_pages_to_exhaustion():
    now = datetime(2026, 7, 22, 15, 13, tzinfo=timezone.utc)
    request = "Give me the candidates I advanced to technical interview last week"
    ledger = GroundingLedger(request, now=now)
    controller = RequiredReadController(ledger, current_user_id=77)
    first_plan = controller.next_plan()
    assert first_plan is not None
    filters = {
        "action": "advanced",
        "target_stage": "Technical Interview",
        "status": "confirmed",
        "actor_id": 77,
        "actor_type": "recruiter",
        "occurred_after": (now - timedelta(days=7)).isoformat(),
        "occurred_before": now.isoformat(),
        "result_view": "candidates",
    }
    first = _exact_page(
        items=[
            {"event_id": event_id, "candidate_name": f"Candidate {event_id}"}
            for event_id in range(1, 101)
        ],
        total=150,
        filters=filters,
    )
    ledger.observe(
        "list_candidate_actions",
        first,
        arguments=first_plan.arguments,
    )
    controller.observe(
        first_plan,
        tool_name="list_candidate_actions",
        result=first,
        arguments=first_plan.arguments,
    )

    second_plan = controller.next_plan()
    assert second_plan is not None
    assert second_plan.tool_name == "list_candidate_actions"
    assert second_plan.arguments == {
        **first_plan.arguments,
        "limit": 100,
        "offset": 100,
    }
    second = _exact_page(
        items=[
            {"event_id": event_id, "candidate_name": f"Candidate {event_id}"}
            for event_id in range(101, 151)
        ],
        total=150,
        offset=100,
        filters=filters,
    )
    ledger.observe(
        "list_candidate_actions",
        second,
        arguments=second_plan.arguments,
    )
    controller.observe(
        second_plan,
        tool_name="list_candidate_actions",
        result=second,
        arguments=second_plan.arguments,
    )

    assert controller.next_plan() is None
    assert CANDIDATE_ACTION_HISTORY_EXHAUSTIVE not in ledger.missing_for_answer("")


def test_required_read_controller_binds_one_tool_and_server_owned_arguments():
    controller = RequiredReadController(GroundingLedger("Show PySpark candidates"))
    plan = controller.next_plan()
    assert plan is not None

    blocks = controller.bind_assistant_blocks(
        plan,
        [
            {
                "type": "tool_use",
                "id": "wrong-1",
                "name": "reject_candidate",
                "input": {"application_id": 999},
            },
            {
                "type": "tool_use",
                "id": "wrong-2",
                "name": "get_role",
                "input": {},
            },
        ],
    )

    assert blocks == [
        {
            "type": "tool_use",
            "id": "wrong-1",
            "name": "find_top_candidates",
            "input": {"query": "Show PySpark candidates", "limit": 10},
        }
    ]


def test_required_read_controller_uses_global_tools_without_inventing_role_scope():
    pool = RequiredReadController(
        GroundingLedger("Who is currently in review?"),
        role_bound=False,
    )
    pool_plan = pool.next_plan()
    assert pool_plan is not None
    assert pool_plan.tool_name == "search_applications"
    assert pool_plan.arguments == {
        "limit": 100,
        "offset": 0,
        "pipeline_stage": "review",
    }
    assert "role_id" not in pool_plan.arguments

    qualitative = RequiredReadController(
        GroundingLedger("Show PySpark candidates"),
        role_bound=False,
    )
    qualitative_plan = qualitative.next_plan()
    assert qualitative_plan is not None
    assert qualitative_plan.tool_name == "find_top_candidates"
    assert "role_id" not in qualitative_plan.arguments

    actions = RequiredReadController(
        GroundingLedger("Who did I advance last week?"),
        role_bound=False,
    )
    assert actions.requires_role_scope is True
    assert actions.next_plan() is None

    exhaustive_pool = RequiredReadController(
        GroundingLedger("List all candidates at Technical Interview"),
        role_bound=False,
    )
    assert exhaustive_pool.requires_role_scope is True
    assert exhaustive_pool.next_plan() is None


@pytest.mark.parametrize(
    "message",
    [
        "Who is currently in technical interview?",
        "Rank the final-interview candidates in full detail",
        "Should I advance Ada?",
        "List rejected candidates",
    ],
)
def test_current_state_or_future_action_questions_require_pool_state(message):
    assert required_capabilities_for_message(message) == frozenset(
        {CANDIDATE_POOL_STATE}
    )


def test_hyphenated_provider_stage_is_bound_as_state_not_cv_experience():
    [claim] = grounding_claims_for_message(
        "Rank the final-interview candidates in full detail"
    )
    assert claim.capability == CANDIDATE_POOL_STATE
    assert claim.filter_map == {"ats_stage": "final interview"}
    assert claim.terms == ()


def test_legacy_event_role_requires_membership_at_the_time_of_the_action():
    event_time = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    event = SimpleNamespace(
        role_id=None,
        created_at=event_time,
        event_metadata={"acting_role_id": 42},
    )
    application = SimpleNamespace(id=7, role_id=1, candidate_id=9)

    def membership(*, created_at, deleted_at=None, source="direct"):
        return SimpleNamespace(
            role_id=42,
            candidate_id=9,
            source_application_id=7,
            ats_application_id=None,
            membership_source=source,
            created_at=created_at,
            deleted_at=deleted_at,
        )

    created_after_action = membership(created_at=event_time + timedelta(days=1))
    assert (
        resolve_historical_event_role_id(
            event,
            application=application,
            memberships=[created_after_action],
            decisions_by_id={},
            decision_applications={},
            valid_role_ids={1, 42},
        )
        == 1
    )

    removed_after_action = membership(
        created_at=event_time - timedelta(days=10),
        deleted_at=event_time + timedelta(days=1),
    )
    assert (
        resolve_historical_event_role_id(
            event,
            application=application,
            memberships=[removed_after_action],
            decisions_by_id={},
            decision_applications={},
            valid_role_ids={1, 42},
        )
        == 42
    )

    removed_before_action = membership(
        created_at=event_time - timedelta(days=10),
        deleted_at=event_time - timedelta(seconds=1),
    )
    assert (
        resolve_historical_event_role_id(
            event,
            application=application,
            memberships=[removed_before_action],
            decisions_by_id={},
            decision_applications={},
            valid_role_ids={1, 42},
        )
        == 1
    )


def test_action_history_application_id_is_stable_across_membership_source_change():
    """A shared ATS link must not make row order choose the model-facing id."""

    event_time = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    event = SimpleNamespace(application_id=7, created_at=event_time)
    source_application = SimpleNamespace(id=7, candidate_id=9)
    old_owner_membership = SimpleNamespace(
        id=10,
        candidate_id=9,
        source_application_id=7,
        ats_application_id=7,
        created_at=event_time - timedelta(days=10),
        deleted_at=event_time + timedelta(days=1),
    )
    live_direct_membership = SimpleNamespace(
        id=11,
        candidate_id=9,
        source_application_id=8,
        ats_application_id=7,
        created_at=event_time + timedelta(days=1),
        deleted_at=None,
    )

    for memberships in (
        [old_owner_membership, live_direct_membership],
        [live_direct_membership, old_owner_membership],
    ):
        assert (
            _canonical_action_application_id(
                event=event,
                source_application=source_application,
                memberships=memberships,
            )
            == 8
        )

    live_direct_membership.deleted_at = event_time + timedelta(days=5)
    for memberships in (
        [old_owner_membership, live_direct_membership],
        [live_direct_membership, old_owner_membership],
    ):
        assert (
            _canonical_action_application_id(
                event=event,
                source_application=source_application,
                memberships=memberships,
            )
            == 7
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
        {CANDIDATE_DECISION_HISTORY_EXHAUSTIVE}
    )


@pytest.mark.parametrize(
    ("message", "expected_status", "expected_prefix"),
    [
        (
            "Which recommendations did I approve last week?",
            "approved",
            "resolved",
        ),
        (
            "List recommendations I overrode last week",
            "overridden",
            "resolved",
        ),
        (
            "Which recommendations were made last week?",
            None,
            "created",
        ),
    ],
)
def test_decision_required_read_binds_the_semantic_time_axis(
    message, expected_status, expected_prefix
):
    now = datetime(2026, 7, 22, 15, 13, tzinfo=timezone.utc)
    ledger = GroundingLedger(message, now=now)
    controller = RequiredReadController(ledger)

    plan = controller.next_plan()

    assert plan is not None
    assert plan.tool_name == "list_recent_agent_decisions"
    if expected_status is None:
        assert "status" not in plan.arguments
    else:
        assert plan.arguments["status"] == expected_status
    assert (
        plan.arguments[f"{expected_prefix}_after"]
        == (now - timedelta(days=7)).isoformat()
    )
    assert plan.arguments[f"{expected_prefix}_before"] == now.isoformat()
    other_prefix = "created" if expected_prefix == "resolved" else "resolved"
    assert f"{other_prefix}_after" not in plan.arguments


def test_created_dates_cannot_certify_a_resolution_date_question():
    now = datetime(2026, 7, 22, 15, 13, tzinfo=timezone.utc)
    request = "Which recommendations did I approve last week?"
    after = (now - timedelta(days=7)).isoformat()
    ledger = GroundingLedger(request, now=now)
    wrong_filters = {
        "status": "approved",
        "created_after": after,
        "created_before": now.isoformat(),
    }
    ledger.observe(
        "list_recent_agent_decisions",
        _exact_page(
            items=[{"id": 1, "candidate_name": "Avery"}],
            total=1,
            filters={"role_id": 42, **wrong_filters},
        ),
        arguments=wrong_filters,
    )

    assert CANDIDATE_DECISION_HISTORY_EXHAUSTIVE in ledger.missing_for_answer("")

    right_filters = {
        "status": "approved",
        "resolved_after": after,
        "resolved_before": now.isoformat(),
    }
    ledger.observe(
        "list_recent_agent_decisions",
        _exact_page(
            items=[{"id": 1, "candidate_name": "Avery"}],
            total=1,
            filters={"role_id": 42, **right_filters},
        ),
        arguments=right_filters,
    )
    assert not ledger.missing_for_answer("")


def test_unprompted_hard_zero_claim_requires_pool_state():
    assert required_capabilities_for_message(
        "Zero candidates have PySpark experience in this pool."
    ) == frozenset({CANDIDATE_QUALITATIVE_EXACT_EMPTY})


@pytest.mark.parametrize(
    "message",
    [
        "Show PySpark candidates",
        "Anyone who knows Agentforce?",
        "Find Salesforce people",
        "Find candidates with Salesforce",
        "Show candidates with banking experience",
        "Avery has PySpark experience supported by their CV.",
    ],
)
def test_qualitative_candidate_requests_require_cited_evidence(message):
    assert required_capabilities_for_message(message) == frozenset(
        {CANDIDATE_QUALITATIVE_EVIDENCE}
    )


def test_non_candidate_chat_does_not_require_candidate_grounding():
    assert required_capabilities_for_message("Hello, can you help me?") == frozenset()


def test_inexact_action_read_cannot_ground_an_exhaustive_history_answer():
    assert (
        capabilities_for_successful_read(
            "list_candidate_actions",
            {"items": [], "total": 0, "total_is_exact": False},
        )
        == frozenset()
    )
    assert capabilities_for_successful_read(
        "list_candidate_actions",
        {"items": [], "total": 0, "total_is_exact": True},
    ) == frozenset({CANDIDATE_ACTION_HISTORY})


def test_inexact_pool_or_decision_reads_cannot_ground_exhaustive_claims():
    assert (
        capabilities_for_successful_read(
            "search_role_candidates",
            {"items": [], "total": 0, "total_is_exact": False},
        )
        == frozenset()
    )
    assert capabilities_for_successful_read(
        "search_role_candidates",
        {"items": [], "total": 0, "total_is_exact": True},
    ) == frozenset({CANDIDATE_POOL_STATE})
    assert (
        capabilities_for_successful_read(
            "list_recent_agent_decisions",
            {"items": [], "total": 0, "total_is_exact": False},
        )
        == frozenset()
    )
    assert capabilities_for_successful_read(
        "list_recent_agent_decisions",
        {"items": [], "total": 0, "total_is_exact": True},
    ) == frozenset({CANDIDATE_DECISION_HISTORY})


def test_legacy_physical_reads_cannot_certify_logical_pool_state():
    legacy_detail = {
        "record_scope": "physical_application_evidence_only",
        "logical_role_state_included": False,
        "application_id": 7,
    }
    legacy_comparison = {
        "record_scope": "physical_application_evidence_only",
        "logical_role_state_included": False,
        "applications": [legacy_detail],
    }

    assert (
        capabilities_for_successful_read("get_application", legacy_detail)
        == frozenset()
    )
    assert (
        capabilities_for_successful_read("compare_applications", legacy_comparison)
        == frozenset()
    )
    assert capabilities_for_successful_read(
        "compare_role_applications",
        {"role": {"id": 42}, "applications": [{"application_id": 7}]},
    ) == frozenset({CANDIDATE_POOL_STATE})


def test_exact_count_does_not_make_a_partial_page_exhaustive():
    partial_actions = {
        "items": [{"event_id": 1}],
        "total": 2,
        "offset": 0,
        "has_more": True,
        "total_is_exact": True,
    }
    action_capabilities = capabilities_for_successful_read(
        "list_candidate_actions", partial_actions
    )
    assert CANDIDATE_ACTION_HISTORY in action_capabilities
    assert CANDIDATE_ACTION_HISTORY_EXHAUSTIVE not in action_capabilities

    complete_actions = {
        **partial_actions,
        "items": [{"event_id": 1}, {"event_id": 2}],
        "has_more": False,
    }
    assert CANDIDATE_ACTION_HISTORY_EXHAUSTIVE in capabilities_for_successful_read(
        "list_candidate_actions", complete_actions
    )

    partial_decisions = {
        "items": [{"id": 1}],
        "total": 2,
        "offset": 0,
        "has_more": True,
        "total_is_exact": True,
    }
    assert (
        CANDIDATE_DECISION_HISTORY_EXHAUSTIVE
        not in capabilities_for_successful_read(
            "list_recent_agent_decisions", partial_decisions
        )
    )


def test_all_candidate_claim_requires_and_receives_only_a_complete_pool_page():
    assert required_capabilities_for_message(
        "Show all candidates currently in review"
    ) == frozenset({CANDIDATE_POOL_EXHAUSTIVE})
    partial = {
        "items": [{"application_id": 1}],
        "total": 2,
        "offset": 0,
        "has_more": True,
        "total_is_exact": True,
    }
    assert CANDIDATE_POOL_EXHAUSTIVE not in capabilities_for_successful_read(
        "search_role_candidates", partial
    )
    complete = {
        **partial,
        "items": [{"application_id": 1}, {"application_id": 2}],
        "has_more": False,
    }
    assert CANDIDATE_POOL_EXHAUSTIVE in capabilities_for_successful_read(
        "search_role_candidates", complete
    )


def test_qualitative_search_only_grounds_positive_or_exact_empty_results():
    assert capabilities_for_successful_read(
        "find_top_candidates",
        {"candidates": [{"application_id": 1}], "is_exact_empty": False},
    ) == frozenset({CANDIDATE_POOL_STATE})
    assert capabilities_for_successful_read(
        "find_top_candidates",
        {"candidates": [], "is_exact_empty": True, "exhaustive": True},
    ) == frozenset({CANDIDATE_POOL_STATE})
    assert (
        capabilities_for_successful_read(
            "find_top_candidates",
            {"candidates": [], "is_exact_empty": False, "exhaustive": False},
        )
        == frozenset()
    )


def test_identity_search_cannot_ground_a_qualitative_claim():
    assert capabilities_for_successful_read(
        "search_role_candidates",
        {"items": [], "total": 0, "total_is_exact": True},
        arguments={"q": "PySpark"},
        request_text="Show PySpark candidates",
    ) == frozenset({CANDIDATE_POOL_STATE})


def test_positive_qualitative_capability_requires_matching_query_and_citation():
    result = {
        "criteria_requested": ["PySpark production experience"],
        "required_criteria": ["PySpark production experience"],
        "criteria_unchecked": [],
        "candidates": [
            {
                "application_id": 1,
                "criteria": [
                    {
                        "criterion": "PySpark production experience",
                        "status": "met",
                        "grounded": True,
                        "evidence": [
                            {
                                "quote": "Built production ETL pipelines in PySpark.",
                                "source": "cv",
                            }
                        ],
                    }
                ],
            }
        ],
        "is_exact_empty": False,
    }
    capabilities = capabilities_for_successful_read(
        "find_top_candidates",
        result,
        arguments={"query": "PySpark production experience"},
        request_text="Show PySpark candidates",
    )
    assert CANDIDATE_QUALITATIVE_EVIDENCE in capabilities

    uncited = {
        **result,
        "candidates": [
            {
                "application_id": 1,
                "criteria": [
                    {
                        "criterion": "PySpark production experience",
                        "status": "met",
                        "grounded": False,
                        "evidence": [],
                    }
                ],
            }
        ],
    }
    assert CANDIDATE_QUALITATIVE_EVIDENCE not in capabilities_for_successful_read(
        "find_top_candidates",
        uncited,
        arguments={"query": "PySpark production experience"},
        request_text="Show PySpark candidates",
    )
    assert CANDIDATE_QUALITATIVE_EVIDENCE not in capabilities_for_successful_read(
        "find_top_candidates",
        result,
        arguments={"query": "Agentforce experience"},
        request_text="Show PySpark candidates",
    )


def test_qualitative_zero_requires_complete_successful_population_evidence():
    exact = {
        "criteria_requested": ["PySpark production experience"],
        "required_criteria": ["PySpark production experience"],
        "criteria_unchecked": [],
        "candidates": [],
        "search_status": "no_verified_matches",
        "qualified_total": 0,
        "capped": False,
        "exhaustive": True,
        "total_matched": 2,
        "pool_size": 2,
        "role_roster_size": 2,
        "deep_checked": 2,
        "evidence_succeeded": 2,
        "is_exact_empty": False,
    }
    capabilities = capabilities_for_successful_read(
        "find_top_candidates",
        exact,
        arguments={"query": "PySpark production experience"},
        request_text="Do we have candidates with PySpark experience?",
    )
    assert {
        CANDIDATE_QUALITATIVE_EVIDENCE,
        CANDIDATE_QUALITATIVE_EXACT_EMPTY,
    } <= capabilities

    for incomplete in (
        {**exact, "capped": True, "exhaustive": False},
        {**exact, "deep_checked": 1, "evidence_succeeded": 1},
        {**exact, "evidence_succeeded": 1},
        {**exact, "criteria_unchecked": ["PySpark production experience"]},
        {**exact, "role_roster_size": 305},
        {**exact, "pool_size": 1},
    ):
        assert (
            CANDIDATE_QUALITATIVE_EXACT_EMPTY
            not in capabilities_for_successful_read(
                "find_top_candidates",
                incomplete,
                arguments={"query": "PySpark production experience"},
                request_text="Do we have candidates with PySpark experience?",
            )
        )


@pytest.mark.parametrize(
    "message",
    [
        "Anyone with PySpark experience?",
        "Are there any PySpark engineers?",
        "Find PySpark engineers",
        "Who has PySpark?",
        "Is there anybody experienced in Agentforce?",
        "Can you find someone with PySpark?",
    ],
)
def test_qualitative_language_variants_require_evidence(message):
    assert CANDIDATE_QUALITATIVE_EVIDENCE in required_capabilities_for_message(message)


@pytest.mark.parametrize(
    "message",
    [
        "Recommend the best candidates",
        "Recommend candidates with PySpark",
        "Who do you recommend?",
    ],
)
def test_future_recommendation_requests_are_not_decision_history(message):
    assert CANDIDATE_DECISION_HISTORY not in required_capabilities_for_message(message)
    assert (
        CANDIDATE_DECISION_HISTORY_EXHAUSTIVE
        not in required_capabilities_for_message(message)
    )


def _exact_page(
    *,
    items,
    total,
    offset=0,
    filters=None,
    role_id=42,
):
    return {
        "role": {"id": role_id, "name": "AI Engineer"},
        "items": items,
        "total": total,
        "offset": offset,
        "limit": max(len(items), 1),
        "total_is_exact": True,
        "has_more": offset + len(items) < total,
        "filters": filters or {},
    }


def test_action_certificate_binds_action_stage_and_time_window():
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    after = now - timedelta(days=7)
    request = "Give me everyone I advanced to Technical Interview last week"
    exact_filters = {
        "action": "advanced",
        "target_stage": "Technical Interview",
        "status": "confirmed",
        "actor_type": "recruiter",
        "actor_id": 7,
        "occurred_after": after.isoformat(),
        "occurred_before": now.isoformat(),
    }
    result = _exact_page(
        items=[{"event_id": 1, "candidate_name": "Avery"}],
        total=1,
        filters=exact_filters,
    )

    grounded = GroundingLedger(request, now=now)
    grounded.bind_current_actor(7)
    grounded.observe(
        "list_candidate_actions",
        result,
        arguments={
            **exact_filters,
            "occurred_after": after,
            "occurred_before": now,
        },
    )
    assert not grounded.missing_for_answer(
        "Avery was advanced to Technical Interview last week."
    )

    for changed in (
        {**exact_filters, "action": "rejected"},
        {**exact_filters, "target_stage": "Final Interview"},
        {
            **exact_filters,
            "occurred_after": (after - timedelta(days=30)).isoformat(),
        },
    ):
        ledger = GroundingLedger(request, now=now)
        ledger.bind_current_actor(7)
        ledger.observe(
            "list_candidate_actions",
            {**result, "filters": changed},
            arguments=changed,
        )
        assert ledger.missing_for_answer(
            "Avery was advanced to Technical Interview last week."
        )


def test_decision_and_pool_certificates_cannot_be_replayed_for_other_filters():
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    decision_request = "List overridden agent recommendations from last week"
    decision_filters = {
        "status": "overridden",
        "resolved_after": (now - timedelta(days=7)).isoformat(),
        "resolved_before": now.isoformat(),
    }
    decision_ledger = GroundingLedger(decision_request, now=now)
    decision_ledger.observe(
        "list_recent_agent_decisions",
        _exact_page(
            items=[{"id": 7}],
            total=1,
            filters={**decision_filters, "role_id": 42},
        ),
        arguments=decision_filters,
    )
    assert not decision_ledger.missing_for_answer(
        "One recommendation was overridden last week."
    )

    wrong_decision = GroundingLedger(decision_request, now=now)
    wrong_decision.observe(
        "list_recent_agent_decisions",
        _exact_page(
            items=[{"id": 8}],
            total=1,
            filters={**decision_filters, "status": "pending", "role_id": 42},
        ),
        arguments={**decision_filters, "status": "pending"},
    )
    assert CANDIDATE_DECISION_HISTORY_EXHAUSTIVE in wrong_decision.missing_for_answer(
        "One recommendation was overridden last week."
    )

    pool_request = "Who is currently in Technical Interview?"
    pool_ledger = GroundingLedger(pool_request, now=now)
    pool_ledger.observe(
        "search_role_candidates",
        _exact_page(
            items=[{"application_id": 9, "candidate_name": "Avery"}],
            total=1,
            filters={"ats_stage": "Technical Interview"},
        ),
        arguments={"ats_stage": "Technical Interview"},
    )
    assert not pool_ledger.missing_for_answer("Avery is in Technical Interview.")

    wrong_pool = GroundingLedger(pool_request, now=now)
    wrong_pool.observe(
        "search_role_candidates",
        _exact_page(
            items=[{"application_id": 9, "candidate_name": "Avery"}],
            total=1,
            filters={"pipeline_stage": "advanced"},
        ),
        arguments={"pipeline_stage": "advanced"},
    )
    assert CANDIDATE_POOL_STATE in wrong_pool.missing_for_answer(
        "Avery is in Technical Interview."
    )


def test_exhaustive_certificate_requires_filter_stable_contiguous_pages():
    request = "Show all candidates currently in review"
    first_items = [{"application_id": value} for value in range(1, 101)]
    final_items = [{"application_id": value} for value in range(101, 131)]
    filters = {
        "pipeline_stage": "review",
        "application_outcome": "open",
        "sort_by": "taali_score",
        "sort_order": "desc",
    }
    ledger = GroundingLedger(request)
    ledger.observe(
        "search_role_candidates",
        _exact_page(items=first_items, total=130, filters=filters),
        arguments={**filters, "limit": 100, "offset": 0},
    )
    assert CANDIDATE_POOL_EXHAUSTIVE in ledger.missing_for_answer(
        "Here are all 130 candidates currently in review."
    )
    ledger.observe(
        "search_role_candidates",
        _exact_page(
            items=final_items,
            total=130,
            offset=100,
            filters=filters,
        ),
        arguments={**filters, "limit": 100, "offset": 100},
    )
    assert not ledger.missing_for_answer(
        "Here are all 130 candidates currently in review."
    )

    unstable = GroundingLedger(request)
    unstable.observe(
        "search_role_candidates",
        _exact_page(items=first_items, total=130, filters=filters),
        arguments={**filters, "limit": 100, "offset": 0},
    )
    unstable.observe(
        "search_role_candidates",
        _exact_page(
            items=final_items,
            total=130,
            offset=100,
            filters={**filters, "pipeline_stage": "advanced"},
        ),
        arguments={
            **filters,
            "pipeline_stage": "advanced",
            "limit": 100,
            "offset": 100,
        },
    )
    assert CANDIDATE_POOL_EXHAUSTIVE in unstable.missing_for_answer(
        "Here are all 130 candidates currently in review."
    )


def test_qualitative_certificate_binds_quality_and_candidate_identity():
    evidence = {
        "criteria_requested": ["PySpark experience"],
        "required_criteria": ["PySpark experience"],
        "criteria_unchecked": [],
        "candidates": [
            {
                "candidate_name": "Avery Stone",
                "criteria": [
                    {
                        "criterion": "PySpark experience",
                        "status": "met",
                        "grounded": True,
                        "evidence": [{"quote": "Built ETL jobs with PySpark."}],
                    }
                ],
            }
        ],
    }
    ledger = GroundingLedger("Show PySpark candidates")
    ledger.observe(
        "find_top_candidates",
        evidence,
        arguments={"query": "PySpark experience"},
    )
    assert not ledger.missing_for_answer(
        "Avery Stone has PySpark experience supported by the CV."
    )
    assert CANDIDATE_QUALITATIVE_EVIDENCE in ledger.missing_for_answer(
        "Jordan Smith has Agentforce experience supported by the CV."
    )


def test_natural_pyspark_answers_reuse_only_the_requested_quality_terms():
    request = "Show PySpark candidates"
    positive = "Avery Stone has PySpark experience, supported by cited CV evidence."
    hard_zero = (
        "Zero candidates with PySpark experience in your pool—the search was "
        "exhaustive (checked everyone) and found none with cited evidence of "
        "PySpark on their CVs."
    )

    assert meaningful_qualitative_terms(positive) == ("pyspark",)
    assert meaningful_qualitative_terms(hard_zero) == ("pyspark",)

    zero_ledger = GroundingLedger(request)
    zero_ledger.observe(
        "find_top_candidates",
        {
            "criteria_requested": ["PySpark experience"],
            "required_criteria": ["PySpark experience"],
            "criteria_unchecked": [],
            "candidates": [],
            "role_roster_size": 4,
            "pool_size": 4,
            "deep_checked": 4,
            "evidence_succeeded": 4,
            "search_status": "no_verified_matches",
            "qualified_total": 0,
            "capped": False,
            "exhaustive": True,
        },
        arguments={"query": "PySpark experience"},
    )
    assert not zero_ledger.missing_for_answer(hard_zero)

    unsupported_extra_claim = (
        hard_zero
        + " The whole pool is LLM-focused and lacks big-data engineering backgrounds."
    )
    assert CANDIDATE_QUALITATIVE_EXACT_EMPTY in zero_ledger.missing_for_answer(
        unsupported_extra_claim
    )


@pytest.mark.parametrize(
    (
        "request_text",
        "tool",
        "items",
        "filters",
        "false_answers",
        "capability",
    ),
    [
        (
            "Who did we advance?",
            "list_candidate_actions",
            [
                {"event_id": 1, "candidate_name": "Alice Stone"},
                {"event_id": 2, "candidate_name": "Carol Reed"},
                {"event_id": 3, "candidate_name": "Dana Fox"},
            ],
            {"action": "advanced"},
            (
                "Zero candidates were advanced.",
                "Bob Jones was advanced.",
                "2 candidates were advanced.",
            ),
            CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
        ),
        (
            "Who is currently in Technical Interview?",
            "search_role_candidates",
            [
                {"application_id": 1, "candidate_name": "Alice Stone"},
                {"application_id": 2, "candidate_name": "Carol Reed"},
                {"application_id": 3, "candidate_name": "Dana Fox"},
            ],
            {"ats_stage": "Technical Interview"},
            (
                "There are zero candidates currently in Technical Interview.",
                "Bob Jones is currently in Technical Interview.",
                "2 candidates are currently in Technical Interview.",
            ),
            CANDIDATE_POOL_STATE,
        ),
        (
            "Which candidates did the agent recommend?",
            "list_recent_agent_decisions",
            [
                {"id": 1, "candidate_name": "Alice Stone"},
                {"id": 2, "candidate_name": "Carol Reed"},
                {"id": 3, "candidate_name": "Dana Fox"},
            ],
            {},
            (
                "Zero recommendations were made.",
                "Bob Jones was recommended.",
                "2 candidates were recommended.",
            ),
            CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
        ),
    ],
)
def test_certificates_bind_terminal_zero_names_and_exact_counts(
    request_text,
    tool,
    items,
    filters,
    false_answers,
    capability,
):
    ledger = GroundingLedger(request_text)
    ledger.observe(
        tool,
        _exact_page(items=items, total=3, filters=filters),
        arguments=filters,
    )

    for false_answer in false_answers:
        assert ledger.missing_for_answer(false_answer)


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


def test_local_pipeline_metadata_cannot_masquerade_as_confirmed_ats_target():
    legacy_local = SimpleNamespace(
        event_type="pipeline_stage_changed",
        event_metadata={"workable_target_stage": "Technical Interview"},
        effect_status="confirmed",
        target_stage=None,
        agent_decision_id=None,
        from_stage="review",
        to_stage="advanced",
        from_outcome="open",
        to_outcome="open",
    )
    confirmed_ats = SimpleNamespace(
        event_type="workable_moved",
        event_metadata={"workable_target_stage": "Technical Interview"},
        effect_status="confirmed",
        target_stage="Technical Interview",
        agent_decision_id=None,
        from_stage=None,
        to_stage=None,
        from_outcome=None,
        to_outcome=None,
    )

    local = _candidate_action_from_event(legacy_local)
    ats = _candidate_action_from_event(confirmed_ats)

    assert local is not None
    assert local["target_stage"] == "advanced"
    assert ats is not None
    assert ats["target_stage"] == "Technical Interview"
