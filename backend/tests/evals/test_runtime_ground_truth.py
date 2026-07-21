"""Offline regressions that join constructed truth to production retrieval code."""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import MagicMock

from app.candidate_graph.search import (
    GraphCandidateEvidenceHit,
    GraphEpisodeEvidence,
    GraphEvidenceSearchResult,
)
from app.candidate_search.evals.contracts import (
    Citation,
    ConstructedWorld,
    Document,
    Fact,
    QueryIntent,
    TruthValue,
    WorldEntity,
)
from app.candidate_search.evals.oracle import derive_judgments
from app.candidate_search import hybrid, runner
from app.candidate_search.hybrid import (
    GraphEvidenceClause,
    GraphEvidenceRequirement,
    run_hybrid_retrieval,
)
from app.candidate_search.retrieval import RetrievalMode
from app.candidate_search.schemas import ParsedFilter
from app.candidate_search.search_plan import (
    Comparison,
    ComparisonOperator,
    Criterion,
    EvidencePolicy,
    Expression,
    Modality,
    Predicate,
    SearchObject,
    SearchPlan,
)


def _document(document_id: str, entity_id: str, content: str) -> Document:
    return Document(
        id=document_id,
        entity_id=entity_id,
        source_type="cv",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def _graph_hit(
    candidate_id: int,
    document: Document,
    *,
    rank: int,
) -> GraphCandidateEvidenceHit:
    return GraphCandidateEvidenceHit(
        candidate_id=candidate_id,
        query="People with hands-on Agentforce experience",
        query_index=0,
        rank=rank,
        edge_uuid=f"edge-{document.id}",
        fact=document.content,
        source_name=None,
        target_name=None,
        episodes=(
            GraphEpisodeEvidence(
                uuid=document.id,
                name=document.id,
                content=document.content,
                source_description=document.source_type,
            ),
        ),
    )


def _query(rows: list[tuple[int, int]]) -> MagicMock:
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    selected = query.with_entities.return_value
    selected.limit.return_value.all.return_value = rows
    selected.limit.return_value.first.return_value = rows[0] if rows else None
    return query


def test_production_hybrid_path_matches_oracle_derived_agentforce_truth(
    monkeypatch,
) -> None:
    """A mention or adjacent Salesforce skill must not become experience."""

    documents = (
        _document(
            "cv-applied",
            "person-applied",
            "Built and deployed Agentforce actions for customer service.",
        ),
        _document(
            "cv-mention",
            "person-mention",
            "Interested in Agentforce and learning the platform.",
        ),
        _document(
            "cv-salesforce",
            "person-salesforce",
            "Administered Salesforce Sales Cloud for five years.",
        ),
        _document(
            "cv-team-attribution",
            "person-team-attribution",
            "Ben's team used Agentforce; Ben did not operate it directly.",
        ),
        _document(
            "cv-negated",
            "person-negated",
            "Did not build or deploy Agentforce in any role.",
        ),
    )
    applied = documents[0]
    start = applied.content.index("Agentforce")
    world = ConstructedWorld(
        id="agentforce-grounding-world",
        entities=tuple(
            WorldEntity(id=document.entity_id, kind="person")
            for document in documents
        ),
        documents=documents,
        facts=(
            Fact(
                id="fact-applied-agentforce",
                subject_id=applied.entity_id,
                predicate="demonstrated",
                object=SearchObject(kind="capability", value="Agentforce"),
                confidence=1.0,
                direct_subject=True,
                provenance=(
                    Citation(
                        document_id=applied.id,
                        start=start,
                        end=start + len("Agentforce"),
                        quote="Agentforce",
                    ),
                ),
            ),
        ),
        closed_world_predicates=("demonstrated",),
    )
    criterion = Criterion(
        id="applied-agentforce",
        predicate=Predicate(name="demonstrated"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="Agentforce"),
        comparison=Comparison(operator=ComparisonOperator.EXISTS),
        modality=Modality.MUST,
        evidence=EvidencePolicy(
            require_direct_subject=True,
            require_citation_span=True,
            minimum_sources=1,
        ),
    )
    intent = QueryIntent(
        id="applied-agentforce",
        plan=SearchPlan(
            query="People with hands-on Agentforce experience",
            criteria=(criterion,),
            root=Expression.leaf(criterion.id),
        ),
    )

    candidate_by_entity = {
        document.entity_id: index
        for index, document in enumerate(documents, start=1)
    }
    judgments = derive_judgments(world, intent)
    expected_candidate_ids = {
        candidate_by_entity[judgment.entity_id]
        for judgment in judgments
        if judgment.eligibility is TruthValue.TRUE
    }
    rejected_candidate_ids = {
        candidate_by_entity[judgment.entity_id]
        for judgment in judgments
        if judgment.eligibility is TruthValue.FALSE
    }
    assert len(expected_candidate_ids) == 1
    assert len(rejected_candidate_ids) == 4
    raw_graph_result = GraphEvidenceSearchResult(
        status="ok",
        # Put both false positives first so filtering, not rank, determines truth.
        hits=tuple(
            _graph_hit(
                candidate_by_entity[document.entity_id],
                document,
                rank=rank,
            )
            for rank, document in enumerate(
                (documents[1], documents[2], documents[3], documents[4], documents[0])
            )
        ),
        exhaustive=True,
    )
    calls: list[dict[str, object]] = []

    def local_graph_search(**kwargs: object) -> GraphEvidenceSearchResult:
        calls.append(kwargs)
        return raw_graph_result

    result = run_hybrid_retrieval(
        query=intent.plan.query,
        organization_id=1,
        allowed_applications={
            candidate_id: candidate_id + 100
            for candidate_id in candidate_by_entity.values()
        },
        postgres=(),
        graph_search_fn=local_graph_search,
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
        graph_requirements=(
            GraphEvidenceRequirement(
                operator="all",
                clauses=(
                    GraphEvidenceClause(
                        clause_id=criterion.id,
                        value=intent.plan.query,
                        predicate=criterion.predicate.name,
                    ),
                ),
            ),
        ),
        mode=RetrievalMode.HYBRID,
    )

    assert len(calls) == 1
    actual_candidate_ids = {hit.candidate_id for hit in result.hits}
    assert actual_candidate_ids == expected_candidate_ids
    assert actual_candidate_ids.isdisjoint(rejected_candidate_ids)
    assert all(
        criterion.id in evidence.clause_ids
        for hit in result.hits
        for evidence in hit.evidence
    )
    assert result.exhaustive is True

    # The same constructed truth now traverses the application-facing runner:
    # parsed filter -> SearchPlan -> graph evidence qualification -> PostgreSQL
    # scope hydration -> fused person/application result and coverage state.
    parsed = ParsedFilter(
        soft_criteria=["hands-on Agentforce experience"],
        free_text=intent.plan.query,
    )
    strict_query = _query([])
    population_rows = [
        (candidate_id + 100, candidate_id)
        for candidate_id in candidate_by_entity.values()
    ]
    population_query = _query(population_rows)
    applied_filters = 0

    def apply_filter(_base, _parsed, **_kwargs):
        nonlocal applied_filters
        applied_filters += 1
        return strict_query if applied_filters == 1 else population_query

    calls.clear()
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", apply_filter)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda query, _parsed: query)
    monkeypatch.setattr(
        runner,
        "retrieve_graph_backend",
        lambda **kwargs: hybrid.retrieve_graph_backend(
            **kwargs,
            graph_search_fn=local_graph_search,
        ),
    )

    search = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query=intent.plan.query,
        base_query=MagicMock(),
    )

    expected_application_ids = {
        candidate_id + 100 for candidate_id in expected_candidate_ids
    }
    assert set(search.application_ids) == expected_application_ids
    assert search.database_matches == 0
    assert search.retrieval_matches == len(expected_application_ids)
    assert search.is_exact_empty is False
    assert len(calls) == 1
