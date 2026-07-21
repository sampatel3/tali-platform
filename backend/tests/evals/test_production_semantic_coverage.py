"""Production-path coverage for the domain-neutral search eval dimensions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.candidate_graph.search import (
    GraphCandidateEvidenceHit,
    GraphEpisodeEvidence,
    GraphEvidenceSearchResult,
)
from app.candidate_search import runner
from app.candidate_search.evals.contracts import Citation, ConstructedDataset, TruthValue
from app.candidate_search.evals.harness import evaluate_ablation
from app.candidate_search.evals.oracle import derive_judgments
from app.candidate_search.evals.production_fusion import (
    FusionEvalInput,
    production_fusion_retriever,
)
from app.candidate_search.evidence_matching import contains_grounding_value
from app.candidate_search.hybrid import (
    GraphEvidenceClause,
    GraphEvidenceRequirement,
    graph_backend_result,
)
from app.candidate_search.plan_adapter import parsed_filter_to_search_plan
from app.candidate_search.plan_evidence import graph_evidence_requirements
from app.candidate_search.retrieval import (
    BackendHit,
    BackendResult,
    BackendStatus,
    EvidenceHit,
    RetrievalMode,
)
from app.candidate_search.runtime_capabilities import unsupported_runtime_requirements
from app.candidate_search.schemas import GraphPredicate, ParsedFilter
from app.candidate_search.search_plan import (
    BooleanOperator,
    ComparisonOperator,
    Expression,
    Modality,
)


def _dataset() -> ConstructedDataset:
    fixture = (
        Path(__file__).parents[2]
        / "app"
        / "candidate_search"
        / "evals"
        / "fixtures"
        / "domain_neutral_v1.json"
    )
    return ConstructedDataset.model_validate_json(fixture.read_text(encoding="utf-8"))


def _operators(expression: Expression) -> set[BooleanOperator]:
    return {expression.operator}.union(
        *(_operators(child) for child in expression.children)
    )


def _graph_hit(candidate_id: int, content: str, *, rank: int) -> GraphCandidateEvidenceHit:
    return GraphCandidateEvidenceHit(
        candidate_id=candidate_id,
        query="constructed local query",
        query_index=0,
        rank=rank,
        edge_uuid=f"edge-{candidate_id}",
        fact=content,
        source_name=None,
        target_name=None,
        episodes=(
            GraphEpisodeEvidence(
                uuid=f"doc-{candidate_id}",
                name=f"doc-{candidate_id}",
                content=content,
                source_description="cv",
            ),
        ),
    )


def _citation(dataset: ConstructedDataset, document_id: str) -> Citation:
    document = next(row for row in dataset.world.documents if row.id == document_id)
    return Citation(
        document_id=document.id,
        start=0,
        end=len(document.content),
        quote=document.content,
    )


def test_constructed_suite_covers_cross_domain_search_semantics() -> None:
    """Prevent the CI eval corpus from collapsing back to one product keyword."""

    dataset = _dataset()
    features: set[str] = set()
    ordered = {
        ComparisonOperator.GT,
        ComparisonOperator.GTE,
        ComparisonOperator.LT,
        ComparisonOperator.LTE,
    }
    relationship_predicates = {"worked_at", "studied_at"}

    for intent in dataset.intents:
        operators = _operators(intent.plan.root)
        if BooleanOperator.ANY in operators:
            features.add("or")
        if BooleanOperator.NOT in operators:
            features.add("not")
        if any(criterion.temporal is not None for criterion in intent.plan.criteria):
            features.add("temporal")
        if any(
            criterion.predicate.name in relationship_predicates
            for criterion in intent.plan.criteria
        ):
            features.add("relationship")
        if any(
            criterion.comparison.operator in ordered
            for criterion in intent.plan.criteria
        ):
            features.add("numeric")
        if any(
            criterion.evidence.minimum_sources > 1
            for criterion in intent.plan.criteria
        ):
            features.add("two_source")
        if all(
            judgment.eligibility is TruthValue.FALSE
            for judgment in derive_judgments(dataset.world, intent)
        ):
            features.add("exact_empty")

    assert features == {
        "or",
        "not",
        "temporal",
        "relationship",
        "numeric",
        "two_source",
        "exact_empty",
    }


def test_plan_and_graph_evidence_adapters_preserve_supported_feature_matrix() -> None:
    """Exercise the real compatibility boundary, not a canned ranked list."""

    class FilterWithExclusions(ParsedFilter):
        excluded_criteria: list[str]

    parsed = FilterWithExclusions(
        skills_any=["Python", "Go"],
        min_years_experience=5,
        graph_predicates=[
            GraphPredicate(type="worked_at", value="Acme"),
            GraphPredicate(type="studied_at", value="Imperial College"),
        ],
        graph_predicate_operator="all",
        soft_criteria=["Python or Go experience"],
        excluded_criteria=["training-only evidence"],
        free_text=(
            "Python or Go experience, five years overall, Acme and Imperial; "
            "exclude training-only evidence"
        ),
    )

    plan = parsed_filter_to_search_plan(parsed)
    requirements = graph_evidence_requirements(parsed, plan)

    assert {BooleanOperator.ANY, BooleanOperator.NOT} <= _operators(plan.root)
    assert {
        criterion.predicate.name
        for criterion in plan.criteria
        if criterion.predicate.name in {"worked_at", "studied_at"}
    } == {"worked_at", "studied_at"}
    duration = next(
        criterion
        for criterion in plan.criteria
        if criterion.predicate.name == "years_experience"
    )
    assert duration.comparison.operator is ComparisonOperator.GTE
    assert duration.comparison.value == 5
    assert any(criterion.modality is Modality.MUST_NOT for criterion in plan.criteria)
    assert any(
        requirement.operator == "any"
        and {clause.value for clause in requirement.clauses} == {"Python", "Go"}
        for requirement in requirements
    )
    assert any(
        requirement.operator == "all"
        and {clause.predicate for clause in requirement.clauses}
        == {"worked_at", "studied_at"}
        for requirement in requirements
    )


def test_graph_adapter_executes_relationship_and_or_evidence_groups() -> None:
    """The production graph adapter must execute boolean evidence requirements."""

    relationships = graph_backend_result(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(1, "Worked at Acme. Located in Dubai.", rank=0),
                _graph_hit(2, "Acme. Dubai.", rank=1),
            ),
            exhaustive=True,
        ),
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
        requirements=(
            GraphEvidenceRequirement(
                "all",
                (
                    GraphEvidenceClause("worked-acme", "Acme", "worked_at"),
                    GraphEvidenceClause("located-dubai", "Dubai", "located_in"),
                ),
            ),
        ),
    )
    alternatives = graph_backend_result(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(3, "Built Go services in production.", rank=0),
                _graph_hit(4, "Built Java services in production.", rank=1),
            ),
            exhaustive=True,
        ),
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
        requirements=(
            GraphEvidenceRequirement(
                "any",
                (
                    GraphEvidenceClause("python", "Python", "demonstrated"),
                    GraphEvidenceClause("go", "Go", "demonstrated"),
                ),
            ),
        ),
    )

    assert [hit.candidate_id for hit in relationships.hits] == [1]
    assert {
        clause_id
        for evidence in relationships.hits[0].evidence
        for clause_id in evidence.clause_ids
    } == {"worked-acme", "located-dubai"}
    assert [hit.candidate_id for hit in alternatives.hits] == [3]
    assert alternatives.hits[0].evidence[0].clause_ids == ("go",)


def test_unprovable_runtime_semantics_are_explicitly_fail_closed() -> None:
    parsed = ParsedFilter(
        skills_all=["Kubernetes"],
        min_years_experience=2,
        soft_criteria=["two years of Kubernetes experience"],
        graph_predicates=[
            GraphPredicate(type="colleague_of", value="candidate-42"),
            GraphPredicate(type="n_hop_from", value="candidate-42", n_hops=2),
        ],
        free_text=(
            "two years of Kubernetes experience and colleague of candidate-42 "
            "within two hops"
        ),
    )

    unsupported = unsupported_runtime_requirements(parsed)

    assert any(value.startswith("exact graph path:") for value in unsupported)
    assert "skill-specific experience duration" in unsupported
    assert not contains_grounding_value(
        "Candidate 42 is mentioned in this CV.",
        "candidate-42",
        predicate="colleague_of",
    )
    assert not contains_grounding_value(
        "Candidate 42 is mentioned in this CV.",
        "candidate-42",
        predicate="n_hop_from",
    )


def test_runner_reports_unprovable_semantics_as_partial_not_exact_zero(
    monkeypatch,
) -> None:
    parsed = ParsedFilter(
        skills_all=["Kubernetes"],
        min_years_experience=2,
        soft_criteria=["two years of Kubernetes experience"],
        graph_predicates=[
            GraphPredicate(type="n_hop_from", value="candidate-42", n_hops=2),
        ],
        free_text=(
            "two years of Kubernetes experience within two hops of candidate-42"
        ),
    )
    base_query = MagicMock()
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)

    result = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query=parsed.free_text or "",
        base_query=base_query,
    )

    assert result.application_ids == []
    assert result.capped is True
    assert result.exhaustive is False
    assert result.is_exact_empty is False
    assert result.search_plan is not None
    warning = next(
        row for row in result.warnings if row.code == "unsupported_search_constraint"
    )
    assert "skill-specific experience duration" in warning.message
    assert "exact graph path: n_hop_from" in warning.message
    # The central live-candidate authorization predicate is always applied;
    # unsupported semantics stop before any retrieval/materialization work.
    base_query.filter.assert_called_once()
    base_query.filter.return_value.with_entities.assert_not_called()


def test_production_fusion_enforces_two_independent_evidence_sources() -> None:
    fixture = _dataset()
    intent = next(row for row in fixture.intents if row.id == "two-source-clearance")
    dataset = fixture.model_copy(
        update={"intents": (intent,), "required_stages": ("final",)}
    )
    criterion_id = intent.plan.criteria[0].id
    evidence = (
        EvidenceHit(
            source="cv",
            reference="episode:p8-clearance-cv",
            clause_ids=(criterion_id,),
        ),
        EvidenceHit(
            source="assessment",
            reference="episode:p8-clearance-assessment",
            clause_ids=(criterion_id,),
        ),
    )
    citations = {
        ("cv", evidence[0].reference): (_citation(dataset, "p8-clearance-cv"),),
        ("assessment", evidence[1].reference): (
            _citation(dataset, "p8-clearance-assessment"),
        ),
    }

    def fusion_input(evidence_rows: tuple[EvidenceHit, ...]) -> FusionEvalInput:
        return FusionEvalInput(
            allowed_applications={8: 108},
            entity_by_candidate_id={8: "p8"},
            graph=BackendResult(
                backend="graph",
                status=BackendStatus.OK,
                hits=(BackendHit(candidate_id=8, evidence=evidence_rows),),
            ),
            citations_by_evidence=citations,
        )

    report = evaluate_ablation(
        dataset,
        backends={
            "one_source": production_fusion_retriever(
                {intent.id: fusion_input(evidence[:1])},
                mode=RetrievalMode.GRAPH_ONLY,
            ),
            "two_sources": production_fusion_retriever(
                {intent.id: fusion_input(evidence)},
                mode=RetrievalMode.GRAPH_ONLY,
            ),
        },
        k=1,
    )
    metrics = {
        variant.backend: variant.queries[0].stages[0].metrics
        for variant in report.variants
    }

    assert metrics["one_source"].supported_citation_count == 1
    assert metrics["one_source"].grounded_hit_count == 0
    assert metrics["two_sources"].supported_citation_count == 2
    assert metrics["two_sources"].grounded_hit_count == 1


def test_production_fusion_preserves_exact_empty_semantics() -> None:
    fixture = _dataset()
    intent = next(row for row in fixture.intents if row.id == "grounded-empty-result")
    dataset = fixture.model_copy(
        update={"intents": (intent,), "required_stages": ("final",)}
    )
    candidate_ids = range(1, len(dataset.world.entities) + 1)
    inputs = {
        intent.id: FusionEvalInput(
            allowed_applications={
                candidate_id: 100 + candidate_id for candidate_id in candidate_ids
            },
            entity_by_candidate_id={
                candidate_id: entity.id
                for candidate_id, entity in zip(candidate_ids, dataset.world.entities)
            },
            graph=BackendResult(
                backend="graph",
                status=BackendStatus.OK,
                hits=(),
                exhaustive=True,
            ),
        )
    }

    report = evaluate_ablation(
        dataset,
        backends={
            "graph": production_fusion_retriever(
                inputs,
                mode=RetrievalMode.GRAPH_ONLY,
            )
        },
        k=8,
    )
    metrics = report.variants[0].queries[0].stages[0].metrics

    assert metrics.relevant_count == 0
    assert metrics.retrieved_count == 0
    assert metrics.recall_at_k == 1.0
    assert metrics.exact_empty_accuracy == 1.0
