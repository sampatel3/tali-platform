"""Zero-provider execution policy for the canonical candidate-search runner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.candidate_search import parser, runner
from app.candidate_search.parser import ProviderCallsForbiddenError


def _query(rows: list[tuple[int, int]]) -> MagicMock:
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    selected = query.with_entities.return_value
    selected.limit.return_value.all.return_value = rows
    return query


def test_forbidden_parser_rejects_ambiguous_query_before_client_resolution(
    monkeypatch,
):
    resolver_calls: list[dict] = []
    monkeypatch.setattr(
        parser,
        "_resolve_anthropic_client",
        lambda **kwargs: resolver_calls.append(kwargs),
    )

    with pytest.raises(ProviderCallsForbiddenError, match="model parser"):
        parser.parse_nl_query(
            "people who transformed a complex treasury operating model",
            organization_id=7,
            provider_mode="forbid",
        )

    assert resolver_calls == []


def test_forbidden_runner_is_deterministic_postgres_only_and_bypasses_cache(
    monkeypatch,
):
    query = _query([(101, 11)])
    parsed_filters = []
    monkeypatch.setattr(
        runner.cache_module,
        "get",
        lambda _key: (_ for _ in ()).throw(
            AssertionError("provider-forbidden search must bypass model cache")
        ),
    )
    monkeypatch.setattr(
        parser,
        "_resolve_anthropic_client",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("model client must not resolve")
        ),
    )

    def _apply(_base, parsed, **_kwargs):
        parsed_filters.append(parsed)
        return query

    monkeypatch.setattr(runner, "apply_searchable_candidate_scope", lambda q, **_kw: q)
    monkeypatch.setattr(runner, "apply_parsed_filter", _apply)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("GraphDB/embedding retrieval must not run")
        ),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        role_id=9,
        nl_query="candidates based in UAE with Python and PostgreSQL",
        base_query=MagicMock(),
        provider_mode="forbid",
    )

    assert len(parsed_filters) == 1
    assert parsed_filters[0].skills_all == ["Python", "PostgreSQL"]
    assert parsed_filters[0].locations_country == ["United Arab Emirates"]
    assert result.application_ids == [101]
    assert result.retrieval is not None
    assert result.retrieval.mode == "postgres_only"
    assert result.retrieval.graph_status == "not_selected"
    assert result.rerank_applied is False


def test_forbidden_runner_rejects_deterministic_semantic_shape_before_graph(
    monkeypatch,
):
    monkeypatch.setattr(runner, "apply_searchable_candidate_scope", lambda q, **_kw: q)
    monkeypatch.setattr(
        runner,
        "apply_parsed_filter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("semantic provider-free request must fail before SQL")
        ),
    )
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("GraphDB/embedding retrieval must not run")
        ),
    )

    with pytest.raises(ProviderCallsForbiddenError, match="semantic retrieval"):
        runner.run_search(
            db=MagicMock(),
            organization_id=7,
            nl_query="candidates with Python experience",
            base_query=MagicMock(),
            provider_mode="forbid",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rerank_enabled": True},
        {"include_subgraph": True},
    ],
)
def test_forbidden_runner_rejects_explicit_provider_branches_before_scope(kwargs):
    with pytest.raises(ProviderCallsForbiddenError, match="Reranking and graph"):
        runner.run_search(
            db=MagicMock(),
            organization_id=7,
            nl_query="Python",
            base_query=MagicMock(),
            provider_mode="forbid",
            **kwargs,
        )
