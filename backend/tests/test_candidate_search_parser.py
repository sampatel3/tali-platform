"""Parser unit tests.

Mocks the Anthropic client so tests never touch the network. The parser
runs in forced tool-use mode (Phase 2): the model emits ParsedFilter as
the tool's ``.input`` dict. Stubs return ``tool_use`` blocks; the
fallback tests use a text response to simulate a model that refused the
tool (the parser fast-fails to keywords-only on any failure).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.candidate_search.deterministic_parser import parse_common_query
from app.candidate_search.parser import _normalise, parse_nl_query
from app.candidate_search.schemas import ParsedFilter


# Tool name the gateway derives from ``ParsedFilter``.
TOOL_NAME = "emit_parsed_filter"


@dataclass
class _StubBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class _StubResponse:
    blocks: list[Any]

    @property
    def content(self):
        return self.blocks


def _text(text: str) -> _StubResponse:
    """Simulate a model that emitted prose instead of using the tool."""
    return _StubResponse(blocks=[_StubBlock(text=text)])


def _tu(input_dict: dict, name: str = TOOL_NAME) -> _StubResponse:
    return _StubResponse(blocks=[_ToolUseBlock(name=name, input=input_dict)])


class _FakeClient:
    """Mimics the Anthropic SDK shape used by the parser."""

    def __init__(
        self,
        *,
        response: _StubResponse | None = None,
        raise_exc: Exception | None = None,
    ):
        self._response = response
        self._raise = raise_exc

        class _Messages:
            def __init__(inner_self):
                inner_self._parent = self
                inner_self.calls: list[dict[str, Any]] = []

            def create(inner_self, **kwargs):
                if inner_self._parent._raise is not None:
                    raise inner_self._parent._raise
                inner_self.calls.append(kwargs)
                return inner_self._parent._response

        self.messages = _Messages()


def _client_for(payload: dict) -> _FakeClient:
    return _FakeClient(response=_tu(payload))


def test_parses_skill_only_query():
    parsed = parse_nl_query(
        "candidates with AWS Glue experience",
        client=_client_for(
            {"skills_all": ["AWS Glue"], "free_text": "candidates with AWS Glue experience"}
        ),
    )
    assert parsed.skills_all == ["AWS Glue"]
    # A request for experience is not satisfied by a skills-array tag alone.
    # Keep the structured term for retrieval, but also preserve the evidence
    # criterion so the grounded search path must verify applied experience.
    assert parsed.soft_criteria == ["AWS Glue experience"]
    assert parsed.free_text == "candidates with AWS Glue experience"


def test_unknown_product_experience_stays_a_generic_required_claim():
    parsed = parse_common_query("Agentforce experience")

    assert parsed is not None
    assert parsed.skills_all == []
    assert parsed.soft_criteria == ["Agentforce experience"]


def test_any_skill_experience_keeps_one_boolean_evidence_criterion():
    parsed = parse_common_query("Python or Kubernetes experience")

    assert parsed is not None
    assert parsed.skills_any == ["Python", "Kubernetes"]
    assert parsed.soft_criteria == ["Python or Kubernetes experience"]


def test_parses_country_query_with_alias_normalisation():
    parsed = parse_nl_query(
        "candidates who have worked in the UK",
        client=_client_for({"locations_country": ["UK"]}),
        organization_id=1,
    )
    assert parsed.locations_country == ["United Kingdom"]


def test_graph_predicate_boolean_operator_survives_parsing():
    parsed = parse_nl_query(
        "candidates who worked at Google or Meta",
        client=_client_for(
            {
                "graph_predicates": [
                    {"type": "worked_at", "value": "Google"},
                    {"type": "worked_at", "value": "Meta"},
                ],
                "graph_predicate_operator": "any",
            }
        ),
        organization_id=1,
    )

    assert parsed.graph_predicate_operator == "any"
    assert [item.value for item in parsed.graph_predicates] == ["Google", "Meta"]


def test_graph_predicates_default_to_all_for_backward_compatibility():
    parsed = ParsedFilter(
        graph_predicates=[{"type": "worked_at", "value": "Google"}]
    )

    assert parsed.graph_predicate_operator == "all"


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
        organization_id=1,
    )
    assert parsed.min_years_experience == 5
    assert parsed.locations_region == ["europe"]
    assert parsed.soft_criteria == ["large enterprise", "in production"]


def test_parses_explicit_preferences_separately_from_required_criteria():
    parsed = parse_nl_query(
        "project manager with Treasury experience, ideally in banking",
        client=_client_for(
            {
                "titles_all": ["project manager"],
                "soft_criteria": ["Treasury experience"],
                "preferred_criteria": ["banking domain experience"],
            }
        ),
        organization_id=1,
    )

    assert parsed.titles_all == ["project manager"]
    assert parsed.soft_criteria == ["Treasury experience"]
    assert parsed.preferred_criteria == ["banking domain experience"]


def test_exact_treasury_banking_pm_query_is_deterministic_and_atomic():
    parser_client = _FakeClient(raise_exc=RuntimeError("must not call the model"))

    parsed = parse_nl_query(
        "project manager with Treasury experience (Banking domain)",
        client=parser_client,
    )

    assert parsed.titles_all == ["project manager"]
    assert parsed.soft_criteria == [
        "Treasury experience within the banking domain"
    ]
    assert parsed.preferred_criteria == []
    assert parser_client.messages.calls == []


def test_natural_request_leading_preserves_pm_population_and_atomic_requirement():
    parser_client = _FakeClient(raise_exc=RuntimeError("must not call the model"))

    parsed = parse_nl_query(
        "can you find a project manager with Treasury experience (Banking domain)",
        client=parser_client,
    )

    assert parsed.titles_all == ["project manager"]
    assert parsed.soft_criteria == [
        "Treasury experience within the banking domain"
    ]
    assert parsed.preferred_criteria == []
    assert parser_client.messages.calls == []


def test_treasury_banking_experience_stays_one_required_criterion():
    parser_client = _FakeClient(raise_exc=RuntimeError("must not call the model"))

    parsed = parse_nl_query(
        "top candidates with Treasury banking experience",
        client=parser_client,
    )

    assert parsed.soft_criteria == ["Treasury banking experience"]
    assert parsed.preferred_criteria == []
    assert parser_client.messages.calls == []


def test_title_quality_fast_path_does_not_swallow_structured_or_negated_prose():
    assert parse_common_query(
        "project manager with 5 years of experience based in UAE"
    ) is None
    assert parse_common_query("candidates with five years experience") is None
    assert parse_common_query("project manager with Python and AWS") is None
    assert parse_common_query("project manager with no banking experience") is None


def test_preference_detection_requires_an_unambiguous_hedge():
    preferred = parse_common_query(
        "top candidates with preference for banking domain experience"
    )
    supplier = parse_common_query(
        "top candidates with preferred supplier experience"
    )
    bonus_payments = parse_common_query(
        "top candidates with bonus payments experience"
    )
    optional = parse_common_query(
        "top candidates with optional banking experience"
    )

    assert preferred is not None
    assert preferred.preferred_criteria == ["banking domain experience"]
    assert supplier is not None
    assert supplier.soft_criteria == ["preferred supplier experience"]
    assert bonus_payments is not None
    assert bonus_payments.soft_criteria == ["bonus payments experience"]
    assert optional is not None
    assert optional.preferred_criteria == ["banking experience"]
    assert parse_common_query(
        "top candidates with preferred banking experience"
    ) is None


def test_title_with_explicit_preference_preserves_title_and_modality():
    for query in (
        "find a project manager with optional banking experience",
        "find a project manager with ideally banking experience",
        "find a project manager with a preference for banking experience",
    ):
        parsed = parse_common_query(query)
        assert parsed is not None
        assert parsed.titles_all == ["project manager"]
        assert parsed.soft_criteria == []
        assert parsed.preferred_criteria == ["banking experience"]

    # Ambiguous domain phrases defer to the richer parser rather than becoming
    # malformed, title-less requirements.
    assert parse_common_query(
        "find a project manager with preferred supplier experience"
    ) is None


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
        organization_id=1,
    )
    assert [p.value for p in parsed.graph_predicates] == ["Google", "Meta"]
    assert all(p.type == "worked_at" for p in parsed.graph_predicates)


def test_text_instead_of_tool_use_falls_back_to_keywords():
    """Model emits prose instead of using the forced tool → parser fast-fails
    to a keyword-only filter so the user still gets ILIKE matches."""
    parsed = parse_nl_query(
        "anything",
        client=_FakeClient(response=_text("not json at all")),
        organization_id=1,
    )
    assert parsed.skills_all == []
    assert parsed.keywords == ["anything"]
    assert parsed.free_text == "anything"
    assert parsed.parse_degraded is True


def test_valid_but_empty_tool_output_is_an_explicit_degraded_fallback():
    parsed = parse_nl_query(
        "anything",
        client=_client_for({}),
        organization_id=1,
    )

    assert parsed.keywords == ["anything"]
    assert parsed.free_text == "anything"
    assert parsed.parse_degraded is True
    assert parsed.is_empty() is False


def test_invalid_schema_falls_back_to_keywords():
    # min_years_experience out of range — schema validation on the tool
    # input fails → fallback.
    parsed = parse_nl_query(
        "ten thousand years",
        client=_client_for({"min_years_experience": 9999}),
        organization_id=1,
    )
    assert parsed.keywords == ["ten thousand years"]
    assert parsed.min_years_experience is None


def test_client_exception_falls_back():
    parsed = parse_nl_query(
        "boom",
        client=_FakeClient(raise_exc=RuntimeError("network down")),
        organization_id=1,
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
        preferred_criteria=[" banking domain ", ""],
    )
    cleaned = _normalise(raw, "")
    assert cleaned.locations_region == ["europe"]
    assert cleaned.locations_country == ["United Kingdom", "Germany"]
    assert cleaned.skills_all == ["Python"]
    assert cleaned.preferred_criteria == ["banking domain"]


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
    # Common skills take the deterministic zero-model path even without a key.
    assert parsed.skills_all == ["AWS Glue"]
    assert parsed.keywords == []


def test_common_title_query_uses_zero_model_parser():
    parsed = parse_nl_query("all candidates with project manager")
    assert parsed.titles_all == ["project manager"]
    assert parsed.skills_all == []


def test_common_skill_set_uses_zero_model_parser():
    parsed = parse_nl_query("all candidates with Python, AWS and Kubernetes")
    assert parsed.skills_all == ["Python", "AWS", "Kubernetes"]


def test_deterministic_country_keeps_canonical_case():
    parsed = parse_nl_query("candidates with Python based in united arab emirates")
    assert parsed.skills_all == ["Python"]
    assert parsed.locations_country == ["United Arab Emirates"]
