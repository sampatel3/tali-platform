"""Fail-closed compatibility coverage for the retired intent parser."""

from __future__ import annotations

import importlib

from app.sub_agents.base import SubAgentRequest
from app.sub_agents.intent_parser import (
    INTENT_PARSER_SUB_AGENT,
    INTENT_PARSER_UNAVAILABLE,
    IntentDirectives,
    _parse_or_empty,
)
from app.sub_agents.registry import all_sub_agents


CANONICAL_SUB_AGENTS = {
    "assessment_scoring",
    "cv_scoring",
    "graph_priors",
    "pre_screen",
    "task_selection",
}


def _request() -> SubAgentRequest:
    return SubAgentRequest(organization_id=1, application_id=2, role_id=3)


def test_compatibility_import_does_not_register_a_sixth_sub_agent() -> None:
    import app.sub_agents.intent_parser as intent_parser

    importlib.reload(intent_parser)

    assert {agent.name for agent in all_sub_agents()} == CANONICAL_SUB_AGENTS


def test_retired_agent_fails_closed_without_database_or_provider_work() -> None:
    result = INTENT_PARSER_SUB_AGENT.run(_request(), db=object())

    assert result.ok is False
    assert result.error == INTENT_PARSER_UNAVAILABLE
    assert result.output == {}
    assert result.tokens_used == 0


def test_directive_schema_remains_available_for_persisted_payloads() -> None:
    parsed = IntentDirectives.model_validate(
        {
            "strictness_modifier": 0.25,
            "must_skills": ["python"],
            "constraints_parsed": [{"kind": "location", "value": "UAE"}],
        }
    )

    assert parsed.strictness_modifier == 0.25
    assert parsed.must_skills == ["python"]
    assert parsed.constraints_parsed[0].value == "UAE"


def test_provider_free_json_helper_degrades_invalid_payloads_to_empty() -> None:
    assert _parse_or_empty("not-json").model_dump() == IntentDirectives().model_dump()
    assert _parse_or_empty(
        '```json\n{"must_skills": ["go"], "strictness_modifier": 0}\n```'
    ).must_skills == ["go"]
