"""Tool-registry and later-turn integration for new Agent Chat commands."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.candidate_search.tool_failure_contract import (
    CANDIDATE_SEARCH_UNAVAILABLE_CODE,
    CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE,
)
from app.agent_chat.engine import persist_user_message, run_agent_response
from app.agent_chat.system_prompt import SYSTEM_PROMPT as AGENT_CHAT_SYSTEM_PROMPT
from app.agent_chat.tools import AGENT_CHAT_TOOLS, dispatch_tool
from app.components.ai_routing.contracts import TaskKey
from app.components.ai_routing.lineage import current_route
from app.models.agent_conversation import (
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.organization import Organization
from app.mcp.provenance import (
    ACTION_HISTORY_REQUIRED_MESSAGE,
    DECISION_HISTORY_REQUIRED_MESSAGE,
    QUALITATIVE_EVIDENCE_REQUIRED_MESSAGE,
)
from app.models.role import Role
from app.models.user import User
from app.taali_chat.system_prompt import SYSTEM_PROMPT as TAALI_CHAT_SYSTEM_PROMPT


def _routed_transport_stub():
    return SimpleNamespace(
        messages=object(),
        ai_routing_metered_transport=True,
        ai_routing_sdk_max_retries=0,
        organization_id=None,
    )


def _world(db):
    org = Organization(name="Command tools org", slug=f"command-tools-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"command-tools-{id(db)}@example.test",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=int(org.id),
        role="owner",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(
        organization_id=int(org.id),
        name="Backend",
        description="A complete backend engineering job specification.",
        source="manual",
    )
    db.add_all([user, role])
    db.flush()
    conversation = AgentConversation(organization_id=int(org.id), role_id=int(role.id))
    db.add(conversation)
    db.flush()
    return user, role, conversation


def _persist_tool_result(db, *, conversation, body):
    row = AgentConversationMessage(
        conversation_id=int(conversation.id),
        organization_id=int(conversation.organization_id),
        role_id=int(conversation.role_id),
        author_role=AUTHOR_ROLE_USER,
        kind=MESSAGE_KIND_TOOL,
        content=[
            {
                "type": "tool_result",
                "tool_use_id": "tool-preview",
                "content": json.dumps(body),
                "is_error": False,
            }
        ],
    )
    db.add(row)
    db.flush()


def _persist_confirmation(db, *, conversation, user):
    row = AgentConversationMessage(
        conversation_id=int(conversation.id),
        organization_id=int(conversation.organization_id),
        role_id=int(conversation.role_id),
        author_role=AUTHOR_ROLE_USER,
        author_user_id=int(user.id),
        kind=MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": "Yes, proceed with that exact preview."}],
        text="Yes, proceed with that exact preview.",
    )
    db.add(row)
    db.flush()


def test_paid_boundaries_never_hold_the_agent_chat_transaction(db):
    """A tool round must not hold FK locks while nested metering runs.

    The production failure behind role 135 was an application-level cycle:
    the worker held the organization row after persisting tool plumbing, then
    waited for the metering session that was waiting on that same row.
    """
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Show me the strongest candidates.",
    )
    db.commit()

    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="overview",
                name="search_role_candidates",
                input={},
            )
        ],
        stop_reason="tool_use",
    )
    final_round = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Here are the strongest candidates.")
        ],
        stop_reason="end_turn",
    )
    responses = iter([tool_round, final_round])
    boundaries: list[tuple[str, bool]] = []
    routed_calls: list[tuple[object, str]] = []
    scoped_routes: list[object] = []
    model_tool_names: list[set[str]] = []

    def model_call(*args, **kwargs):
        boundaries.append(("model", db.in_transaction()))
        routed_calls.append((args[0], kwargs["model"]))
        model_tool_names.append({str(tool["name"]) for tool in kwargs.get("tools", [])})
        return next(responses)

    def run_tool(*_args, **_kwargs):
        boundaries.append(("tool", db.in_transaction()))
        scoped_routes.append(current_route())
        return {"items": [{"application_id": 1}], "total": 1, "total_is_exact": True}

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=SimpleNamespace(
                messages=object(),
                ai_routing_metered_transport=True,
                ai_routing_sdk_max_retries=0,
                organization_id=None,
            ),
        ) as resolver,
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", side_effect=model_call),
        patch("app.agent_chat.engine.dispatch_tool", side_effect=run_tool),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert assistant.text == "Here are the strongest candidates."
    assert boundaries == [("model", False), ("tool", False), ("model", False)]
    routed_client = routed_calls[0][0]
    assert routed_calls == [
        (routed_client, "claude-haiku-4-5-20251001"),
        (routed_client, "claude-haiku-4-5-20251001"),
    ]
    route = resolver.call_args.args[0]
    assert route.decision.task is TaskKey.ROLE_CHAT_ORCHESTRATION
    assert route.attribution.organization_id == organization.id
    assert route.attribution.role_id == role.id
    assert route.attribution.user_id == user.id
    assert route.attribution.entity_id == str(conversation.id)
    assert scoped_routes == [route]
    assert current_route() is None
    assert route.terminal_status is None
    db.commit()
    assert route.terminal_status == "cancelled"
    assert assistant.model == "claude-haiku-4-5-20251001"
    resolver.assert_called_once_with(route)
    assert all(
        {
            "search_role_candidates",
            "get_role_candidate",
            "list_candidate_actions",
            "list_recent_agent_decisions",
        }
        <= names
        for names in model_tool_names
    )


def test_agent_chat_withholds_unsupported_historical_candidate_claim(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message=(
            "Give me the candidates I advanced to technical interview last week"
        ),
    )
    db.commit()
    unsupported = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text="Zero candidates were advanced last week.",
            )
        ],
        stop_reason="end_turn",
    )

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", return_value=unsupported),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert assistant.text == ACTION_HISTORY_REQUIRED_MESSAGE
    assert assistant.stop_reason == "grounding_required"


@pytest.mark.parametrize(
    ("user_message", "unsupported", "expected"),
    [
        (
            "Hello",
            "Zero candidates have PySpark experience in this pool.",
            QUALITATIVE_EVIDENCE_REQUIRED_MESSAGE,
        ),
        (
            "Show the pending agent decisions",
            "There are no pending agent recommendations.",
            DECISION_HISTORY_REQUIRED_MESSAGE,
        ),
    ],
)
def test_agent_chat_withholds_unread_pool_and_decision_claims(
    db, user_message, unsupported, expected
):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message=user_message,
    )
    db.commit()
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=unsupported)],
        stop_reason="end_turn",
    )

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", return_value=response),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert unsupported not in assistant.text
    assert assistant.text == expected
    assert assistant.stop_reason == "grounding_required"


def test_identity_only_role_search_cannot_ground_pyspark_zero(db):
    """Regression: q searches names/positions, never CV skill evidence."""

    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Show PySpark candidates",
    )
    db.commit()
    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="identity-search",
                name="search_role_candidates",
                input={"q": "PySpark"},
            )
        ],
        stop_reason="tool_use",
    )
    false_zero = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text="Zero candidates have PySpark experience in this pool.",
            )
        ],
        stop_reason="end_turn",
    )

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch(
            "app.agent_chat.engine.one_call",
            side_effect=[tool_round, false_zero],
        ),
        patch(
            "app.agent_chat.engine.dispatch_tool",
            return_value={
                "items": [],
                "total": 0,
                "total_is_exact": True,
                "has_more": False,
                "filters": {"q": "PySpark"},
            },
        ),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert assistant.text == QUALITATIVE_EVIDENCE_REQUIRED_MESSAGE
    assert assistant.stop_reason == "grounding_required"


def test_cited_pyspark_result_can_ground_agent_chat_answer(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Show PySpark candidates",
    )
    db.commit()
    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="evidence-search",
                name="find_top_candidates",
                input={"query": "PySpark experience"},
            )
        ],
        stop_reason="tool_use",
    )
    supported_text = "Avery has PySpark experience, supported by the cited CV evidence."
    final_round = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=supported_text)],
        stop_reason="end_turn",
    )
    evidence_result = {
        "type": "candidate_evidence",
        "criteria_requested": ["PySpark experience"],
        "required_criteria": ["PySpark experience"],
        "criteria_unchecked": [],
        "candidates": [
            {
                "application_id": 1,
                "candidate_name": "Avery",
                "criteria": [
                    {
                        "criterion": "PySpark experience",
                        "status": "met",
                        "grounded": True,
                        "evidence": [
                            {
                                "quote": "Built streaming services with PySpark.",
                                "source": "cv",
                            }
                        ],
                    }
                ],
            }
        ],
        "returned": 1,
        "deep_checked": 1,
        "evidence_succeeded": 1,
        "search_status": "matches_found",
        "capped": False,
        "exhaustive": True,
        "is_exact_empty": False,
        "warnings": [],
    }

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch(
            "app.agent_chat.engine.one_call",
            side_effect=[tool_round, final_round],
        ),
        patch("app.agent_chat.engine.dispatch_tool", return_value=evidence_result),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert assistant.text == supported_text
    assert assistant.stop_reason == "end_turn"


@pytest.mark.parametrize(
    ("user_message", "expected_tool", "supported_text"),
    [
        (
            "Give me the candidates I advanced to technical interview last week",
            "list_candidate_actions",
            "Avery was advanced to technical interview last week.",
        ),
        (
            "Show PySpark candidates",
            "find_top_candidates",
            "Avery has PySpark experience supported by cited CV evidence.",
        ),
        (
            "Who is currently in technical interview?",
            "search_role_candidates",
            "Avery is currently in technical interview.",
        ),
        (
            "Show the pending agent decisions",
            "list_recent_agent_decisions",
            "There is one pending agent decision.",
        ),
    ],
)
def test_agent_chat_forces_canonical_grounded_reads_with_bound_filters(
    db,
    user_message,
    expected_tool,
    supported_text,
):
    """Offline runtime matrix shared with the global Taali Chat contract."""

    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message=user_message,
    )
    db.commit()
    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="forced-read",
                name="adjust_agent_settings",
                input={"auto_promote": True},
            )
        ],
        stop_reason="tool_use",
    )
    final_round = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=supported_text)],
        stop_reason="end_turn",
    )
    dispatched: list[tuple[str, dict]] = []

    def grounded_result(name, arguments, **_kwargs):
        dispatched.append((name, dict(arguments)))
        filters = {
            key: value
            for key, value in arguments.items()
            if key not in {"limit", "offset"}
        }
        if name == "list_candidate_actions":
            return {
                "role": {"id": int(role.id), "name": role.name},
                "items": [
                    {
                        "event_id": 1,
                        "candidate_name": "Avery",
                        "action": "advanced",
                        "target_stage": "Technical Interview",
                    }
                ],
                "total": 1,
                "limit": arguments["limit"],
                "offset": arguments["offset"],
                "total_is_exact": True,
                "has_more": False,
                "filters": filters,
            }
        if name == "search_role_candidates":
            return {
                "role": {"id": int(role.id), "name": role.name},
                "items": [{"application_id": 1, "candidate_name": "Avery"}],
                "total": 1,
                "limit": arguments["limit"],
                "offset": arguments["offset"],
                "total_is_exact": True,
                "has_more": False,
                "filters": filters,
            }
        if name == "list_recent_agent_decisions":
            return {
                "items": [{"id": 1, "candidate_name": "Avery"}],
                "total": 1,
                "limit": arguments["limit"],
                "offset": arguments["offset"],
                "total_is_exact": True,
                "has_more": False,
                "filters": {"role_id": int(role.id), **filters},
            }
        assert name == "find_top_candidates"
        return {
            "type": "candidate_evidence",
            "criteria_requested": ["PySpark experience"],
            "required_criteria": ["PySpark experience"],
            "criteria_unchecked": [],
            "candidates": [
                {
                    "application_id": 1,
                    "candidate_name": "Avery",
                    "criteria": [
                        {
                            "criterion": "PySpark experience",
                            "status": "met",
                            "grounded": True,
                            "evidence": [
                                {
                                    "quote": "Built production pipelines in PySpark.",
                                    "source": "cv",
                                }
                            ],
                        }
                    ],
                }
            ],
            "qualified_total": 1,
            "search_status": "matches_found",
            "capped": False,
            "exhaustive": True,
            "is_exact_empty": False,
            "warnings": [],
        }

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch(
            "app.agent_chat.engine.one_call",
            side_effect=[tool_round, final_round],
        ) as model_call,
        patch(
            "app.agent_chat.engine.dispatch_tool",
            side_effect=grounded_result,
        ),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert assistant.text == supported_text
    assert [name for name, _ in dispatched] == [expected_tool]
    sent = dispatched[0][1]
    assert "role_id" not in sent
    assert sent["limit"] in {10, 100}
    if expected_tool == "list_candidate_actions":
        assert sent["action"] == "advanced"
        assert sent["target_stage"] == "technical interview"
        assert sent["status"] == "confirmed"
        assert sent["occurred_after"] < sent["occurred_before"]
    elif expected_tool == "find_top_candidates":
        assert sent["query"] == user_message
    elif expected_tool == "search_role_candidates":
        assert sent["ats_stage"] == "technical interview"
    else:
        assert sent["status"] == "pending"
    assert model_call.call_args_list[0].kwargs["tool_choice"] == {
        "type": "tool",
        "name": expected_tool,
        "disable_parallel_tool_use": True,
    }


def test_agent_chat_continues_exhaustive_action_pages_before_answering(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message=(
            "Give me the candidates I advanced to technical interview last week"
        ),
    )
    db.commit()
    tool_rounds = [
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id=f"action-page-{page}",
                    name="get_role_candidate",
                    input={"application_id": 999_999},
                )
            ],
            stop_reason="tool_use",
        )
        for page in (1, 2)
    ]
    final_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text=("150 candidates were advanced to technical interview last week."),
            )
        ],
        stop_reason="end_turn",
    )
    offsets: list[int] = []

    def action_page(name, arguments, **_kwargs):
        assert name == "list_candidate_actions"
        offset = int(arguments["offset"])
        offsets.append(offset)
        returned = 100 if offset == 0 else 50
        filters = {
            key: value
            for key, value in arguments.items()
            if key not in {"limit", "offset"}
        }
        return {
            "role": {"id": int(role.id), "name": role.name},
            "items": [
                {
                    "event_id": offset + index + 1,
                    "candidate_name": f"Candidate {offset + index + 1}",
                }
                for index in range(returned)
            ],
            "total": 150,
            "limit": arguments["limit"],
            "offset": offset,
            "total_is_exact": True,
            "has_more": offset + returned < 150,
            "filters": filters,
        }

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch(
            "app.agent_chat.engine.one_call",
            side_effect=[*tool_rounds, final_round],
        ) as model_call,
        patch("app.agent_chat.engine.dispatch_tool", side_effect=action_page),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    assert assistant.text.startswith("150 candidates were advanced")
    assert offsets == [0, 100]
    assert [
        call.kwargs["tool_choice"]["name"] for call in model_call.call_args_list[:2]
    ] == ["list_candidate_actions", "list_candidate_actions"]


def test_registry_exposes_every_new_command_once():
    names = [tool["name"] for tool in AGENT_CHAT_TOOLS]
    assert len(names) == len(set(names))
    assert {
        "search_role_candidates",
        "get_role_candidate",
        "list_candidate_actions",
        "list_recent_agent_decisions",
        "get_recruiting_overview",
        "list_pending_decisions",
        "approve_decision",
        "override_decision",
        "snooze_decision",
        "re_evaluate_decision",
        "teach_decision",
        "get_helper_briefing",
        "list_recent_agent_runs",
        "list_open_recruiter_inputs",
        "answer_recruiter_input",
        "dismiss_recruiter_input",
        "create_application",
        "add_internal_note",
        "run_agent_now",
        "start_related_role_draft",
    }.issubset(names)
    assert "post_workable_note" not in names


def test_related_role_prompts_describe_independent_logical_pool_seeding():
    tool_text = json.dumps(AGENT_CHAT_TOOLS).lower()
    prompt_text = f"{AGENT_CHAT_SYSTEM_PROMPT}\n{TAALI_CHAT_SYSTEM_PROMPT}".lower()
    combined = f"{prompt_text}\n{tool_text}"

    assert "selected logical role's explicit candidate pool" in combined
    assert "ats link is transport/restrictions only" in combined
    for stale in (
        "cousin",
        "sister",
        "this ats role's existing applicants",
        "this ats role's existing candidate pool",
    ):
        assert stale not in combined


def test_approve_decision_previews_then_executes_after_later_confirmation(db):
    user, role, conversation = _world(db)
    snapshot = {
        "decision_id": 42,
        "application_id": 99,
        "candidate_name": "Ada Lovelace",
        "decision_type": "send_assessment",
        "recommendation": "send_assessment",
        "role_family": {
            "owner": {"id": int(role.id), "name": "Backend"},
            "related": [],
        },
        "reasoning": "Strong match",
        "confidence": 0.91,
        "created_at": "2026-07-14T12:00:00+00:00",
        "snoozed_until": None,
        "can_approve": True,
        "approval_requires_workable_stage": False,
        "supported_alternatives": ["reject", "skip_assessment_advance"],
        "is_stale": False,
        "staleness_reasons": [],
        "staleness_summary": None,
    }
    with (
        patch(
            "app.agent_chat.tools._decision_commands.get_pending_decision",
            return_value=snapshot,
        ),
        patch(
            "app.agent_chat.tools._decision_commands.approve_decision",
            return_value={"status": "processing", "decision_id": 42},
        ) as execute,
    ):
        preview = dispatch_tool(
            "approve_decision",
            {"decision_id": 42, "note": "Strong evidence"},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "decision_action_preview"
        assert preview["needs_confirmation"] is True
        assert preview["decision"]["role_family"] == snapshot["role_family"]
        execute.assert_not_called()

        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)
        receipt = dispatch_tool(
            "approve_decision",
            {"decision_id": 42, "note": "Strong evidence"},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert receipt["type"] == "operation_receipt"
    assert receipt["status"] == "processing"
    assert receipt["_confirmation_consumed"]
    execute.assert_called_once_with(
        db,
        role,
        user,
        decision_id=42,
        note="Strong evidence",
        workable_target_stage=None,
    )


def test_decision_confirmation_is_repreviewed_when_role_family_changes(db):
    user, role, conversation = _world(db)
    snapshot = {
        "decision_id": 44,
        "application_id": 101,
        "candidate_name": "Katherine Johnson",
        "decision_type": "reject",
        "recommendation": "reject",
        "status": "pending",
        "created_at": "2026-07-14T12:00:00+00:00",
        "can_approve": True,
        "approval_requires_workable_stage": False,
        "supported_alternatives": ["send_assessment", "advance"],
        "is_stale": False,
        "staleness_reasons": [],
        "role_family": {
            "owner": {"id": int(role.id), "name": "Backend"},
            "related": [{"id": 71, "name": "API Engineer"}],
        },
    }
    changed_snapshot = {
        **snapshot,
        "role_family": {
            **snapshot["role_family"],
            "related": [
                *snapshot["role_family"]["related"],
                {"id": 72, "name": "Data Engineer"},
            ],
        },
    }
    with (
        patch(
            "app.agent_chat.tools._decision_commands.get_pending_decision",
            side_effect=[snapshot, changed_snapshot],
        ),
        patch(
            "app.agent_chat.tools._decision_commands.approve_decision",
            return_value={"status": "processing", "decision_id": 44},
        ) as execute,
    ):
        preview = dispatch_tool(
            "approve_decision",
            {"decision_id": 44},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)

        refreshed = dispatch_tool(
            "approve_decision",
            {"decision_id": 44},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert refreshed["type"] == "decision_action_preview"
    assert refreshed["needs_confirmation"] is True
    assert refreshed["decision"]["role_family"] == changed_snapshot["role_family"]
    assert "fresh preview" in refreshed["message"]
    execute.assert_not_called()


def test_teach_decision_previews_then_records_exact_confirmed_feedback(db):
    user, role, conversation = _world(db)
    snapshot = {
        "decision_id": 43,
        "application_id": 100,
        "candidate_name": "Grace Hopper",
        "decision_type": "reject",
        "recommendation": "reject",
        "status": "pending",
        "reasoning": "Missing evidence",
        "created_at": "2026-07-14T12:00:00+00:00",
    }
    arguments = {
        "decision_id": 43,
        "failure_mode": "missing_signal",
        "correction_text": "Use the verified portfolio evidence before rejecting.",
        "scope": "role",
        "attributed_to": "cv_scoring",
        "direction": "under",
    }
    with (
        patch(
            "app.agent_chat.tools._decision_teach.get_teachable_decision",
            return_value=snapshot,
        ),
        patch(
            "app.agent_chat.tools._decision_commands.teach_decision",
            return_value={
                "decision_status": "reverted_for_feedback",
                "feedback_id": 8,
                "cosign_required": False,
            },
        ) as execute,
    ):
        preview = dispatch_tool(
            "teach_decision",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "decision_action_preview"
        execute.assert_not_called()

        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)
        receipt = dispatch_tool(
            "teach_decision",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert receipt["type"] == "operation_receipt"
    assert receipt["result"]["feedback_id"] == 8
    execute.assert_called_once_with(db, role, user, **arguments)


def test_create_application_previews_then_uses_canonical_confirmed_arguments(db):
    user, role, conversation = _world(db)
    preview_data = {
        "type": "create_application_preview",
        "role_id": int(role.id),
        "candidate_email": "ada@example.com",
        "candidate_name": "Ada",
        "candidate_position": None,
        "candidate_exists": False,
        "candidate_id": None,
        "application_exists": False,
        "application_id": None,
        "would_update_candidate_profile": False,
        "can_create": True,
        "blocked_reason": None,
    }
    with (
        patch(
            "app.agent_chat.tools._application_commands.preview_create_application",
            return_value=preview_data,
        ),
        patch(
            "app.agent_chat.tools._application_commands.create_application",
            return_value={
                "status": "created",
                "application_id": 123,
                "candidate_id": 456,
                "candidate_email": "ada@example.com",
            },
        ) as execute,
    ):
        arguments = {"candidate_email": " ADA@example.com ", "candidate_name": "Ada"}
        preview = dispatch_tool(
            "create_application",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "operation_preview"
        execute.assert_not_called()

        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)
        receipt = dispatch_tool(
            "create_application",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert receipt["type"] == "operation_receipt"
    assert receipt["result"]["application_id"] == 123
    execute.assert_called_once_with(
        db,
        role,
        user,
        candidate_email="ada@example.com",
        candidate_name="Ada",
        candidate_position=None,
        notes=None,
    )


def test_model_round_cannot_batch_two_state_changes(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Set the threshold and enable auto promote.",
    )

    def response(blocks, stop_reason):
        return SimpleNamespace(
            content=blocks,
            stop_reason=stop_reason,
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    tool_round = response(
        [
            SimpleNamespace(
                type="tool_use",
                id="threshold",
                name="set_threshold",
                input={"threshold": 65},
            ),
            SimpleNamespace(
                type="tool_use",
                id="settings",
                name="adjust_agent_settings",
                input={"auto_promote": True},
            ),
        ],
        "tool_use",
    )
    final_round = response(
        [SimpleNamespace(type="text", text="I need to run those one at a time.")],
        "end_turn",
    )

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", side_effect=[tool_round, final_round]),
        patch("app.agent_chat.engine.dispatch_tool") as execute,
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    execute.assert_not_called()
    assert assistant.text == "I need to run those one at a time."
    tool_results = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_TOOL,
            AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
        )
        .order_by(AgentConversationMessage.id.desc())
        .first()
    )
    assert len(tool_results.content) == 2
    assert all(block["is_error"] is True for block in tool_results.content)
    assert all(
        "one state-changing command" in block["content"]
        for block in tool_results.content
    )


def test_candidate_search_failure_is_terminal_sanitized_and_precedes_mutation(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Set the threshold, then find the strongest PySpark candidates.",
    )
    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="mutation-first-in-model-output",
                name="set_threshold",
                input={"threshold": 70},
            ),
            SimpleNamespace(
                type="tool_use",
                id="search-second-in-model-output",
                name="find_top_candidates",
                input={"query": "PySpark experience"},
            ),
        ],
        stop_reason="tool_use",
    )
    raw_marker = str(organization.slug)
    dispatched: list[str] = []

    def fail_search(name, *_args, **_kwargs):
        dispatched.append(name)
        tool_db = _kwargs["db"]
        tool_db.add(Organization(name="Duplicate", slug=raw_marker))
        tool_db.flush()  # real IntegrityError leaves the Session rollback-only
        raise AssertionError("duplicate organization flush should fail")

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", return_value=tool_round) as model_call,
        patch("app.agent_chat.engine.dispatch_tool", side_effect=fail_search),
        patch.object(db, "rollback", wraps=db.rollback) as rollback,
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )
    db.commit()  # proves the terminal transcript survives failed-transaction recovery

    assert assistant.text == CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE
    assert assistant.stop_reason == CANDIDATE_SEARCH_UNAVAILABLE_CODE
    assert model_call.call_count == 1
    assert dispatched == ["find_top_candidates"]
    rollback.assert_called_once()

    hidden_result = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_TOOL,
            AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
        )
        .order_by(AgentConversationMessage.id.desc())
        .first()
    )
    # The server-required qualitative read binds the first provider envelope
    # to ``find_top_candidates`` and discards every additional untrusted tool
    # call before persistence. The apparent mutation is therefore never a
    # mutation, and the second provider call cannot survive as an executable
    # transcript entry.
    assert [block["tool_use_id"] for block in hidden_result.content] == [
        "mutation-first-in-model-output",
    ]
    serialized = json.dumps(hidden_result.content)
    assert "search-second-in-model-output" not in serialized
    assert raw_marker not in serialized
    assert "find_top_candidates" in serialized
    assert CANDIDATE_SEARCH_UNAVAILABLE_CODE in serialized


@pytest.mark.parametrize(
    "failure_shape",
    [
        {"warnings": [{"code": "rerank_skipped"}], "candidates": []},
        {"warnings": [{"code": "search_plan_failed"}], "applications": []},
        {
            "warnings": [{"code": "evidence_incomplete"}],
            "candidates": [],
            "returned": 0,
            "evidence_succeeded": 0,
            "is_exact_empty": False,
        },
        {
            "search_status": "structural_retrieval_incomplete",
            "warnings": [{"code": "structural_retrieval_incomplete"}],
            "candidates": [],
            "returned": 0,
            "pool_size": 2,
            "role_roster_size": 5,
            "qualified_total": None,
            "exhaustive": False,
            "is_exact_empty": False,
        },
    ],
)
def test_warning_shaped_search_failure_is_terminal_and_discards_raw_message(
    db, failure_shape
):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Find PySpark candidates.",
    )
    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="warning-search",
                name="find_top_candidates",
                input={"query": "PySpark experience"},
            )
        ],
        stop_reason="tool_use",
    )
    raw_marker = "raw reranker/provider exception"
    failure_shape["warnings"][0]["message"] = raw_marker

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch(
            "app.agent_chat.engine.one_call",
            side_effect=[tool_round],
        ) as model_call,
        patch(
            "app.agent_chat.engine.dispatch_tool",
            return_value=failure_shape,
        ),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )
    db.commit()

    assert assistant.text == CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE
    assert model_call.call_count == 1
    transcript = json.dumps(
        [
            row.content
            for row in db.query(AgentConversationMessage)
            .filter(AgentConversationMessage.conversation_id == conversation.id)
            .all()
        ]
    )
    assert CANDIDATE_SEARCH_UNAVAILABLE_CODE in transcript
    assert raw_marker not in transcript


def test_unexpected_non_search_tool_error_is_sanitized_before_model_followup(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    raw_marker = str(organization.slug)
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Show the role overview.",
    )
    tool_round = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="overview",
                name="get_role_overview",
                input={},
            )
        ],
        stop_reason="tool_use",
    )
    final_round = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="That tool could not be completed.")
        ],
        stop_reason="end_turn",
    )

    def fail_tool(_name, *_args, **kwargs):
        tool_db = kwargs["db"]
        tool_db.add(Organization(name="Duplicate", slug=raw_marker))
        tool_db.flush()
        raise AssertionError("duplicate organization flush should fail")

    with (
        patch(
            "app.agent_chat.engine.routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch("app.agent_chat.engine.reserve"),
        patch(
            "app.agent_chat.engine.one_call",
            side_effect=[tool_round, final_round],
        ) as model_call,
        patch("app.agent_chat.engine.dispatch_tool", side_effect=fail_tool),
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )
    db.commit()

    assert assistant.text == "That tool could not be completed."
    assert model_call.call_count == 2
    model_visible = json.dumps(model_call.call_args_list[1].kwargs["messages"])
    assert "tool_execution_failed" in model_visible
    assert raw_marker not in model_visible
    persisted = json.dumps(
        [
            row.content
            for row in db.query(AgentConversationMessage)
            .filter(AgentConversationMessage.conversation_id == conversation.id)
            .all()
        ]
    )
    assert "tool_execution_failed" in persisted
    assert raw_marker not in persisted
