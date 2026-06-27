"""Intent-parser sub-agent: cache + parse-failure tolerance."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app.sub_agents.base import SubAgentRequest
from app.sub_agents.intent_parser import INTENT_PARSER_SUB_AGENT, IntentDirectives

from .conftest import make_full_application


def _fake_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(payload), type="text")],
        usage=SimpleNamespace(input_tokens=120, output_tokens=60),
    )


def test_empty_slots_skips_claude_call(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        extra={"slots": {"must_have": "", "preferred": "", "nice_to_have": "", "constraints": ""}},
    )
    with patch("app.sub_agents.intent_parser.get_client_for_org") as resolver:
        result = INTENT_PARSER_SUB_AGENT.run(req, db=db)
    resolver.assert_not_called()
    assert result.ok is True
    assert result.confidence == 0.0
    parsed = IntentDirectives.model_validate(result.output)
    assert parsed.must_skills == []


def test_well_formed_response_validates_into_directives(db):
    org, role, _, app = make_full_application(db)
    payload = {
        "strictness_modifier": 0.4,
        "must_skills": ["python", "kubernetes"],
        "disqualifying_signals": ["no production experience"],
        "soft_signals": ["fintech background"],
        "constraints_parsed": [
            {"kind": "location", "value": "EU only", "detail": None}
        ],
    }
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        extra={
            "slots": {
                "must_have": "python; kubernetes",
                "preferred": "fintech background",
                "nice_to_have": "",
                "constraints": "EU candidates only",
            }
        },
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: _fake_response(payload))
    )
    with patch(
        "app.sub_agents.intent_parser.get_client_for_org", return_value=fake_client
    ):
        result = INTENT_PARSER_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.cache_hit is False
    assert result.output["must_skills"] == ["python", "kubernetes"]
    assert result.output["strictness_modifier"] == 0.4
    # Cache-miss success path must surface the token count read off
    # response.usage (regression guard: this line used to reference
    # undefined `in_tok`/`out_tok` and crash the whole sub-agent).
    assert result.tokens_used == 180  # 120 input + 60 output


def test_cache_miss_success_tolerates_missing_usage(db):
    """The Claude response may lack a ``usage`` block (older stubs, some
    proxies). The cache-miss success path must still return ok=True with
    ``tokens_used`` defaulting to 0 — never raise on the missing attr."""
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        extra={
            "slots": {
                "must_have": "rust",
                "preferred": "",
                "nice_to_have": "",
                "constraints": "",
            }
        },
    )
    payload = {
        "strictness_modifier": 0.0,
        "must_skills": ["rust"],
        "disqualifying_signals": [],
        "soft_signals": [],
        "constraints_parsed": [],
    }
    # No ``usage`` attribute on the response at all.
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(payload), type="text")]
            )
        )
    )
    with patch(
        "app.sub_agents.intent_parser.get_client_for_org", return_value=fake_client
    ):
        result = INTENT_PARSER_SUB_AGENT.run(req, db=db)
    assert result.ok is True
    assert result.cache_hit is False
    assert result.output["must_skills"] == ["rust"]
    assert result.tokens_used == 0


def test_invalid_json_returns_empty_directives(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        extra={
            "slots": {
                "must_have": "anything",
                "preferred": "",
                "nice_to_have": "",
                "constraints": "",
            }
        },
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[SimpleNamespace(text="not json at all", type="text")],
                usage=SimpleNamespace(input_tokens=50, output_tokens=10),
            )
        )
    )
    with patch(
        "app.sub_agents.intent_parser.get_client_for_org", return_value=fake_client
    ):
        result = INTENT_PARSER_SUB_AGENT.run(req, db=db)
    # Sub-agent recovers with empty directives.
    assert result.ok is True
    assert result.output["must_skills"] == []


def test_cache_hit_skips_claude_call(db):
    org, role, _, app = make_full_application(db)
    slots = {"must_have": "go", "preferred": "", "nice_to_have": "", "constraints": ""}
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        extra={"slots": slots},
    )
    payload = {
        "strictness_modifier": 0.0,
        "must_skills": ["go"],
        "disqualifying_signals": [],
        "soft_signals": [],
        "constraints_parsed": [],
    }
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: _fake_response(payload))
    )
    with patch(
        "app.sub_agents.intent_parser.get_client_for_org", return_value=fake_client
    ):
        first = INTENT_PARSER_SUB_AGENT.run(req, db=db)
    assert first.ok and not first.cache_hit

    # Second call: same slots -> cache hit, no Claude call (resolver
    # not called).
    with patch("app.sub_agents.intent_parser.get_client_for_org") as resolver:
        second = INTENT_PARSER_SUB_AGENT.run(req, db=db)
    resolver.assert_not_called()
    assert second.ok and second.cache_hit
