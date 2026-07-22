"""Runner wiring for generic hybrid recall and auditable plans."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.candidate_graph.search import (
    GraphCandidateEvidenceHit,
    GraphEpisodeEvidence,
    GraphEvidenceSearchResult,
)
from app.candidate_search import hybrid, runner
from app.candidate_search.schemas import GraphPayload, ParsedFilter


def _query(rows: list[tuple[int, int]]) -> MagicMock:
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.with_entities.return_value.all.return_value = rows
    query.with_entities.return_value.limit.return_value.all.return_value = rows
    return query


def _graph_hit(candidate_id: int) -> GraphCandidateEvidenceHit:
    return GraphCandidateEvidenceHit(
        candidate_id=candidate_id,
        query="Agentforce experience",
        query_index=0,
        rank=0,
        edge_uuid="generated-edge-context",
        fact="Generated paraphrase must not become a citation",
        source_name="Candidate",
        target_name="Agentforce",
        episodes=(
            GraphEpisodeEvidence(
                uuid="candidate-note-1",
                name="candidate-1-note",
                content="Built and deployed Agentforce actions for service workflows.",
                source_description="candidate_note",
            ),
        ),
    )


def test_semantic_graph_hit_rescues_postgres_miss_inside_structured_population(
    monkeypatch,
):
    parsed = ParsedFilter(
        skills_all=["Agentforce"],
        titles_all=["AI Engineer"],
        soft_criteria=["hands-on Agentforce experience"],
        free_text="AI engineers with hands-on Agentforce experience",
    )
    postgres_query = _query([(20, 2)])
    population_query = _query([(10, 1), (20, 2), (30, 3)])
    applied: list[ParsedFilter] = []

    def _apply(_base, candidate_filter, **_kwargs):
        applied.append(candidate_filter)
        return postgres_query if len(applied) == 1 else population_query

    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "parse_common_query", lambda _query: None)
    monkeypatch.setattr(runner, "apply_parsed_filter", _apply)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda query, _parsed: query)
    captured: dict = {}

    def _hybrid(**kwargs):
        captured.update(kwargs)
        return hybrid.run_hybrid_retrieval(**kwargs)

    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **kwargs: hybrid.retrieve_graph_backend(
            **kwargs,
            graph_search_fn=lambda **_graph_kwargs: GraphEvidenceSearchResult(
                status="ok",
                hits=(_graph_hit(1),),
                exhaustive=True,
            ),
        ),
    )

    monkeypatch.setattr(runner, "run_hybrid_retrieval", _hybrid)

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        role_id=9,
        nl_query="AI engineers with hands-on Agentforce experience",
        base_query=MagicMock(),
    )

    assert result.application_ids == [10, 20]
    assert captured["allowed_applications"] == {1: 10, 2: 20}
    assert applied[1].titles_all == ["AI Engineer"]
    assert applied[1].skills_all == []
    assert applied[1].soft_criteria == []
    assert result.search_plan is not None
    assert result.search_plan["version"] == "1.0"
    assert result.retrieval is not None
    assert result.retrieval.mode == "hybrid"
    assert result.retrieval.graph_coverage is None
    assert result.retrieval.hits[0].sources == ["graph"]
    evidence = result.retrieval.hits[0].evidence[0]
    assert evidence["source"] == "candidate_note"
    assert evidence["reference"] == "episode:candidate-note-1"
    assert len(evidence["clause_ids"]) == 2
    assert any(
        clause_id.startswith("skill-all-agentforce-")
        for clause_id in evidence["clause_ids"]
    )
    assert any(
        clause_id.startswith("required-claim-hands-on-agentforce-experience-")
        for clause_id in evidence["clause_ids"]
    )
    assert result.exhaustive is False
    assert any(w.code == "graph_coverage_partial" for w in result.warnings)


def test_generic_product_experience_returns_only_source_grounded_graph_candidate(
    monkeypatch,
):
    parsed = ParsedFilter(
        soft_criteria=["Agentforce experience"],
        free_text="Agentforce experience",
    )
    postgres_query = _query([])
    population_query = _query([(10, 1), (20, 2)])
    applied = 0

    def _apply(_base, _candidate_filter, **_kwargs):
        nonlocal applied
        applied += 1
        return postgres_query if applied == 1 else population_query

    invalid = GraphCandidateEvidenceHit(
        candidate_id=2,
        query="Agentforce experience",
        query_index=0,
        rank=1,
        edge_uuid="salesforce-only",
        fact="Generated similarity is not evidence",
        source_name="Candidate",
        target_name="Salesforce",
        episodes=(
            GraphEpisodeEvidence(
                uuid="candidate-note-2",
                name="candidate-2-note",
                content="Worked as a Salesforce administrator.",
                source_description="candidate_note",
            ),
        ),
    )
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "parse_common_query", lambda _query: None)
    monkeypatch.setattr(runner, "apply_parsed_filter", _apply)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda query, _parsed: query)
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **kwargs: hybrid.retrieve_graph_backend(
            **kwargs,
            graph_search_fn=lambda **_graph_kwargs: GraphEvidenceSearchResult(
                status="ok",
                hits=(_graph_hit(1), invalid),
                exhaustive=True,
            ),
        ),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        role_id=9,
        nl_query="Agentforce experience",
        base_query=MagicMock(),
    )

    assert result.application_ids == [10]
    assert result.database_matches == 0
    assert result.retrieval_matches == 1
    assert result.retrieval is not None
    assert result.retrieval.hits[0].candidate_id == 1
    assert result.retrieval.hits[0].evidence[0]["clause_ids"][0].startswith(
        "required-claim-agentforce-experience-"
    )


def test_hybrid_mapping_prefers_open_application_over_ranked_closed_one(monkeypatch):
    parsed = ParsedFilter(
        soft_criteria=["Agentforce experience"],
        free_text="Agentforce experience",
    )
    closed_ranked_query = _query([(20, 1)])
    active_first_scope = _query([(10, 1), (20, 1)])
    applied = 0

    def _apply(_base, _candidate_filter, **_kwargs):
        nonlocal applied
        applied += 1
        return closed_ranked_query if applied == 1 else active_first_scope

    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", _apply)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda query, _parsed: query)
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **kwargs: hybrid.retrieve_graph_backend(
            **kwargs,
            graph_search_fn=lambda **_graph_kwargs: GraphEvidenceSearchResult(
                status="ok",
                hits=(_graph_hit(1),),
                exhaustive=True,
            ),
        ),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="Agentforce experience",
        base_query=MagicMock(),
    )

    assert result.application_ids == [10]
    assert result.database_matches == 1


def test_graph_unavailable_empty_is_not_reported_as_exact_zero(monkeypatch):
    parsed = ParsedFilter(
        soft_criteria=["unusual reconciliation incident experience"],
        free_text="unusual reconciliation incident experience",
    )
    query = _query([])
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *_args, **_kwargs: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **kwargs: hybrid.retrieve_graph_backend(
            **kwargs,
            graph_search_fn=lambda **_graph_kwargs: GraphEvidenceSearchResult(
                status="unavailable"
            ),
        ),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="unusual reconciliation incident experience",
        base_query=MagicMock(),
    )

    assert result.application_ids == []
    assert result.database_matches == 0
    assert result.exhaustive is False
    assert result.is_exact_empty is False
    assert result.retrieval is not None
    assert result.retrieval.graph_status == "unavailable"
    assert result.retrieval.is_exact_empty is False
    assert any(w.code == "graph_retrieval_unavailable" for w in result.warnings)


def test_search_plan_failure_empty_fallback_is_not_reported_as_exact_zero(
    monkeypatch,
):
    parsed = ParsedFilter(
        soft_criteria=["unusual reconciliation incident experience"],
        free_text="unusual reconciliation incident experience",
    )
    query = _query([])
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *_args, **_kwargs: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)
    monkeypatch.setattr(
        runner,
        "parsed_filter_to_search_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad plan")),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="unusual reconciliation incident experience",
        base_query=MagicMock(),
    )

    assert result.application_ids == []
    assert result.search_plan is None
    assert result.exhaustive is False
    assert result.is_exact_empty is False
    assert any(warning.code == "search_plan_failed" for warning in result.warnings)


def test_empty_authorized_population_skips_semantic_graph_retrieval(monkeypatch):
    parsed = ParsedFilter(
        soft_criteria=["unusual reconciliation incident experience"],
        free_text="unusual reconciliation incident experience",
    )
    strict_query = _query([])
    population_query = _query([])
    population_query.with_entities.return_value.limit.return_value.first.return_value = None
    applied = 0

    def _apply(_base, _candidate_filter, **_kwargs):
        nonlocal applied
        applied += 1
        return strict_query if applied == 1 else population_query

    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", _apply)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)
    graph_calls: list[dict] = []
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **kwargs: graph_calls.append(kwargs),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="unusual reconciliation incident experience",
        base_query=MagicMock(),
    )

    assert graph_calls == []
    assert result.application_ids == []
    assert result.is_exact_empty is True
    assert result.exhaustive is True


def test_skill_bound_duration_fails_closed_until_typed_duration_is_executable(
    monkeypatch,
):
    parsed = ParsedFilter(
        skills_all=["AWS Glue"],
        min_years_experience=3,
        soft_criteria=["AWS Glue production experience"],
        free_text="3 years of AWS Glue production experience",
    )
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "parse_common_query", lambda _query: None)
    monkeypatch.setattr(
        runner,
        "apply_parsed_filter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported semantics must fail before retrieval")
        ),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="3 years of AWS Glue production experience",
        base_query=MagicMock(),
    )

    assert result.application_ids == []
    assert result.capped is True
    assert result.exhaustive is False
    assert result.is_exact_empty is False
    assert any(
        warning.code == "unsupported_search_constraint"
        and "skill-specific experience duration" in warning.message
        for warning in result.warnings
    )


@pytest.mark.parametrize("predicate_type", ["colleague_of", "n_hop_from"])
def test_exact_path_predicates_fail_closed_with_explicit_capability_warning(
    monkeypatch,
    predicate_type,
):
    parsed = ParsedFilter(
        graph_predicates=[
            {
                "type": predicate_type,
                "value": "candidate-42",
                **({"n_hops": 2} if predicate_type == "n_hop_from" else {}),
            }
        ],
        free_text="candidates connected to candidate 42",
    )
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(
        runner,
        "apply_parsed_filter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported paths must fail before retrieval")
        ),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="candidates connected to candidate 42",
        base_query=MagicMock(),
    )

    assert result.application_ids == []
    assert result.exhaustive is False
    assert result.is_exact_empty is False
    assert any(
        warning.code == "unsupported_search_constraint"
        and predicate_type in warning.message
        for warning in result.warnings
    )


def test_plain_structured_search_uses_postgres_without_graph_execution(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"], free_text="Python")
    query = _query([(10, 1)])
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *_args, **_kwargs: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)
    graph_calls: list[dict] = []
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **graph_kwargs: graph_calls.append(graph_kwargs),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="Python",
        base_query=MagicMock(),
    )

    assert result.application_ids == [10]
    assert graph_calls == []
    assert result.retrieval is not None
    assert result.retrieval.mode == "postgres_only"
    assert result.retrieval.graph_status == "not_selected"
    assert result.exhaustive is True
    assert result.is_exact_empty is False


def test_exhaustive_postgres_zero_is_explicitly_safe_to_report(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"], free_text="Python")
    query = _query([])
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *_args, **_kwargs: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="Python",
        base_query=MagicMock(),
    )

    assert result.application_ids == []
    assert result.is_exact_empty is True
    assert result.retrieval is not None
    assert result.retrieval.is_exact_empty is True


def test_graph_view_never_substitutes_unrelated_query_topology(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"], free_text="Python")
    query = _query([(10, 1)])
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *_args, **_kwargs: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda value, _parsed: value)
    monkeypatch.setattr(
        runner,
        "_candidate_ids_for_application_ids",
        lambda _db, _application_ids: [1],
    )

    from app.candidate_graph import search as graph_search

    monkeypatch.setattr(
        graph_search,
        "subgraph_for_candidates",
        lambda **_kwargs: GraphPayload(),
    )
    broad_query_calls: list[dict] = []
    monkeypatch.setattr(
        graph_search,
        "subgraph_for_query",
        lambda **kwargs: broad_query_calls.append(kwargs),
    )

    result = runner.run_search(
        db=MagicMock(),
        organization_id=7,
        nl_query="Python",
        base_query=MagicMock(),
        include_subgraph=True,
    )

    assert broad_query_calls == []
    assert result.subgraph == GraphPayload()
    assert any(
        warning.code == "graph_coverage_partial" for warning in result.warnings
    )
