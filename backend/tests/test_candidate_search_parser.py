"""Parser unit tests.

Mocks the Anthropic client so tests never touch the network. Asserts:
- Each example query parses to the expected ``ParsedFilter``.
- Malformed JSON falls back to keywords-only.
- Empty queries produce an empty filter without calling Claude.
- Country and region aliases normalise.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.candidate_search.parser import _normalise, parse_nl_query
from app.candidate_search.schemas import ParsedFilter


class _FakeClient:
    """Mimics the Anthropic SDK shape used by the parser."""

    def __init__(self, response_text: str | None = None, raise_exc: Exception | None = None):
        self._response_text = response_text or ""
        self._raise = raise_exc

        class _Messages:
            def __init__(inner_self):
                inner_self._parent = self

            def create(inner_self, **kwargs):
                if inner_self._parent._raise is not None:
                    raise inner_self._parent._raise
                return SimpleNamespace(
                    content=[SimpleNamespace(text=inner_self._parent._response_text)]
                )

        self.messages = _Messages()


def _client_for(payload: dict) -> _FakeClient:
    return _FakeClient(response_text=json.dumps(payload))


def test_parses_skill_only_query():
    parsed = parse_nl_query(
        "candidates with AWS Glue experience",
        client=_client_for(
            {"skills_all": ["AWS Glue"], "free_text": "candidates with AWS Glue experience"}
        ),
    )
    assert parsed.skills_all == ["AWS Glue"]
    assert parsed.free_text == "candidates with AWS Glue experience"


def test_parses_country_query_with_alias_normalisation():
    parsed = parse_nl_query(
        "candidates who have worked in the UK",
        client=_client_for({"locations_country": ["UK"]}),
    )
    assert parsed.locations_country == ["United Kingdom"]


def test_parses_compound_query_with_region_and_soft_criteria():
    parsed = parse_nl_query(
        "5 years experience, worked in Europe, large enterprise in production",
        client=_client_for(
            {
                "min_years_experience": 5,
                "locations_region": ["europe"],
                "soft_criteria": ["large enterprise", "in production"],
            }
        ),
    )
    assert parsed.min_years_experience == 5
    assert parsed.locations_region == ["europe"]
    assert parsed.soft_criteria == ["large enterprise", "in production"]


def test_parses_graph_predicates():
    parsed = parse_nl_query(
        "Python, worked at Google or Meta",
        client=_client_for(
            {
                "skills_all": ["Python"],
                "graph_predicates": [
                    {"type": "worked_at", "value": "Google"},
                    {"type": "worked_at", "value": "Meta"},
                ],
            }
        ),
    )
    assert [p.value for p in parsed.graph_predicates] == ["Google", "Meta"]
    assert all(p.type == "worked_at" for p in parsed.graph_predicates)


def test_malformed_json_falls_back_to_keywords():
    parsed = parse_nl_query("anything", client=_FakeClient(response_text="not json at all"))
    assert parsed.skills_all == []
    assert parsed.keywords == ["anything"]
    assert parsed.free_text == "anything"


def test_invalid_schema_falls_back_to_keywords():
    # min_years_experience out of range — ValidationError → fallback.
    parsed = parse_nl_query(
        "ten thousand years",
        client=_client_for({"min_years_experience": 9999}),
    )
    assert parsed.keywords == ["ten thousand years"]
    assert parsed.min_years_experience is None


def test_client_exception_falls_back():
    parsed = parse_nl_query(
        "boom",
        client=_FakeClient(raise_exc=RuntimeError("network down")),
    )
    assert parsed.keywords == ["boom"]


def test_empty_query_short_circuits_without_claude_call():
    # Pass a client that would raise if called: the parser must not call it.
    parser_client = _FakeClient(raise_exc=RuntimeError("must not be called"))
    parsed = parse_nl_query("   ", client=parser_client)
    assert parsed.is_empty()
    assert parsed.free_text == ""


def test_normalise_drops_unknown_regions():
    raw = ParsedFilter(
        locations_region=["europe", "atlantis"],
        locations_country=["uk", "Germany"],
        skills_all=[" Python ", ""],
    )
    cleaned = _normalise(raw, "")
    assert cleaned.locations_region == ["europe"]
    assert cleaned.locations_country == ["United Kingdom", "Germany"]
    assert cleaned.skills_all == ["Python"]


def test_no_api_key_falls_back():
    # Force missing client by passing client=None and ensuring settings.ANTHROPIC_API_KEY is empty.
    import app.candidate_search.parser as parser_module
    original = parser_module._resolve_anthropic_client

    def boom():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    parser_module._resolve_anthropic_client = boom
    try:
        parsed = parse_nl_query("AWS Glue", client=None)
    finally:
        parser_module._resolve_anthropic_client = original
    assert parsed.keywords == ["AWS Glue"]
