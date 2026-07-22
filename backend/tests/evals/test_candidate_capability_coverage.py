"""Cheap, deterministic coverage gates for candidate-facing agent capabilities.

These checks do not call a model.  They make the shared catalogue the release
contract: every agent receives the same role-bound tools, and each tool carries
the provenance fields needed to distinguish current state, completed actions,
and recommendations.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.agent_chat.tools import AGENT_CHAT_TOOLS
from app.agent_chat import tools as agent_chat_tools
from app.agent_runtime.tool_registry import AGENT_TOOLS
from app.agent_runtime import tool_registry as autonomous_tools
from app.candidate_search.tool_failure_contract import is_candidate_search_tool
from app.mcp import handlers as candidate_handlers
from app.mcp import server as mcp_server
from app.mcp.catalog import (
    AGENT_CHAT,
    AUTONOMOUS_AGENT,
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_POOL_STATE,
    PUBLIC_MCP,
    TAALI_CHAT,
    get_tool_spec,
)
from app.mcp.server import mcp_app
from app.taali_chat.tool_registry import TAALI_CHAT_TOOLS
from app.taali_chat import tool_registry as taali_tools


CANONICAL_TOOLS = {
    "search_role_candidates",
    "get_role_candidate",
    "list_candidate_actions",
    "list_recent_agent_decisions",
}
ALL_AGENT_SURFACES = {
    PUBLIC_MCP,
    TAALI_CHAT,
    AGENT_CHAT,
    AUTONOMOUS_AGENT,
}


def _tool_names(definitions: list[dict]) -> set[str]:
    return {str(definition["name"]) for definition in definitions}


def _definitions_by_name(definitions: list[dict]) -> dict[str, dict]:
    return {str(definition["name"]): definition for definition in definitions}


def test_candidate_capabilities_are_exposed_on_every_agent_surface() -> None:
    public_names = {
        tool.name for tool in asyncio.run(mcp_app.list_tools())
    }

    assert CANONICAL_TOOLS <= public_names
    assert CANONICAL_TOOLS <= _tool_names(TAALI_CHAT_TOOLS)
    assert CANONICAL_TOOLS <= _tool_names(AGENT_CHAT_TOOLS)
    assert CANONICAL_TOOLS <= _tool_names(AGENT_TOOLS)
    for name in CANONICAL_TOOLS:
        assert get_tool_spec(name).exposures == ALL_AGENT_SURFACES
    assert is_candidate_search_tool("search_role_candidates")


def test_autonomous_governance_cannot_hide_grounding_reads() -> None:
    role = SimpleNamespace(agent_action_allowlist=[])

    exposed = _tool_names(autonomous_tools.tools_for_role(role))

    assert CANONICAL_TOOLS <= exposed


def test_candidate_tool_definitions_are_generated_from_the_shared_catalogue() -> None:
    taali = _definitions_by_name(TAALI_CHAT_TOOLS)
    agent_chat = _definitions_by_name(AGENT_CHAT_TOOLS)
    autonomous = _definitions_by_name(AGENT_TOOLS)

    for name in CANONICAL_TOOLS:
        spec = get_tool_spec(name)
        assert taali[name] == spec.anthropic_definition(bound_role=False)
        assert agent_chat[name] == spec.anthropic_definition(bound_role=True)
        assert autonomous[name] == spec.anthropic_definition(bound_role=True)


def test_canonical_candidate_tools_execute_the_same_handlers_on_every_surface() -> None:
    db = object()
    principal = SimpleNamespace(organization_id=7, id=3)
    role = SimpleNamespace(id=42, organization_id=7)
    conversation = SimpleNamespace(role_id=42)
    tool_arguments = {
        "search_role_candidates": {"application_outcome": "open", "limit": 10},
        "get_role_candidate": {"application_id": 11},
        "list_candidate_actions": {"status": "confirmed", "limit": 10},
        "list_recent_agent_decisions": {"status": "pending", "limit": 10},
    }

    @contextmanager
    def borrowed_session(_ctx, _scopes):
        yield db, principal

    with patch.object(mcp_server, "_open_session", borrowed_session):
        for name, arguments in tool_arguments.items():
            sentinel = {"canonical_handler": name}
            with patch.object(
                candidate_handlers,
                name,
                return_value=sentinel,
            ) as handler:
                public_result = getattr(mcp_server, name)(
                    object(),
                    role_id=42,
                    **arguments,
                )
                taali_result = taali_tools.dispatch_tool(
                    name,
                    arguments,
                    db=db,
                    user=principal,
                    conversation=conversation,
                )
                agent_chat_result = agent_chat_tools.dispatch_tool(
                    name,
                    arguments,
                    db=db,
                    role=role,
                    user=principal,
                )
                autonomous_result = autonomous_tools.dispatch(
                    name,
                    arguments,
                    db=db,
                    role=role,
                    agent_run=SimpleNamespace(decisions_emitted=0),
                )

            assert public_result is sentinel
            assert taali_result is sentinel
            assert agent_chat_result is sentinel
            assert autonomous_result is sentinel
            assert handler.call_count == 4
            for call in handler.call_args_list:
                assert call.args[0] is db
                assert int(call.args[1].organization_id) == 7
                assert call.kwargs["role_id"] == 42


def test_role_bound_agents_cannot_spoof_candidate_capability_role_id() -> None:
    for name in CANONICAL_TOOLS:
        spec = get_tool_spec(name)
        assert spec.role_scoped is True
        bound_schema = spec.anthropic_definition(bound_role=True)["input_schema"]
        assert "role_id" not in bound_schema.get("properties", {})
        assert "role_id" not in bound_schema.get("required", [])


def test_candidate_capability_schemas_preserve_time_and_claim_provenance() -> None:
    search = get_tool_spec("search_role_candidates")
    detail = get_tool_spec("get_role_candidate")
    actions = get_tool_spec("list_candidate_actions")
    decisions = get_tool_spec("list_recent_agent_decisions")

    assert search.capabilities == frozenset({CANDIDATE_POOL_STATE})
    assert detail.capabilities == frozenset(
        {"candidate.detail", CANDIDATE_POOL_STATE}
    )
    assert actions.capabilities == frozenset({CANDIDATE_ACTION_HISTORY})
    assert decisions.capabilities == frozenset({CANDIDATE_DECISION_HISTORY})

    action_properties = actions.input_schema["properties"]
    assert {
        "action",
        "status",
        "target_stage",
        "actor_type",
        "occurred_after",
        "occurred_before",
        "application_id",
        "candidate_id",
    } <= set(action_properties)
    decision_properties = decisions.input_schema["properties"]
    assert {
        "status",
        "created_after",
        "created_before",
        "resolved_after",
        "resolved_before",
        "application_id",
        "candidate_id",
        "decision_type",
    } <= set(decision_properties)


def test_tool_descriptions_forbid_history_claims_from_the_wrong_source() -> None:
    search_description = get_tool_spec("search_role_candidates").description.lower()
    action_description = get_tool_spec("list_candidate_actions").description.lower()
    decision_description = get_tool_spec(
        "list_recent_agent_decisions"
    ).description.lower()

    assert "current" in search_description
    assert "confirmed" in action_description
    assert "pending recommendations are not completed actions" in action_description
    assert "not proof" in decision_description
    assert "list_candidate_actions" in decision_description
