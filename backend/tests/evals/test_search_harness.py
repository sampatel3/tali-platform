"""Offline backend-ablation harness tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.candidate_search.evals.contracts import (
    ConstructedDataset,
    RankedHit,
    RetrievalCase,
    RetrievalStageOutput,
)
from app.candidate_search.evals.harness import evaluate_ablation
from app.candidate_search.evals.production_fusion import (
    FusionEvalInput,
    production_fusion_retriever,
)
from app.candidate_search.retrieval import (
    BackendHit,
    BackendResult,
    BackendStatus,
    EvidenceHit,
    RetrievalMode,
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


def _backend(
    rankings: dict[str, list[str]],
) -> Callable[[RetrievalCase], tuple[RetrievalStageOutput, ...]]:
    def retrieve(
        case: RetrievalCase,
    ) -> tuple[RetrievalStageOutput, ...]:
        assert not hasattr(case, "world")
        assert not hasattr(case, "plan")
        assert not hasattr(case.corpus, "facts")
        entity_ids = rankings[case.id]
        lexical = tuple(
            RankedHit(entity_id=entity_id, score=1.0 / (index + 1))
            for index, entity_id in enumerate(reversed(entity_ids))
        )
        final = tuple(
            RankedHit(entity_id=entity_id, score=1.0 / (index + 1))
            for index, entity_id in enumerate(entity_ids)
        )
        return (
            RetrievalStageOutput(stage="retrieval", hits=lexical),
            RetrievalStageOutput(stage="final", hits=final),
        )

    return retrieve


def test_harness_compares_graph_postgres_and_hybrid_from_same_oracle() -> None:
    dataset = _dataset()
    intent_ids = [intent.id for intent in dataset.intents]
    graph_rankings = {intent_id: ["p2", "p1", "p3"] for intent_id in intent_ids}
    postgres_rankings = {intent_id: ["p4", "p3", "p2"] for intent_id in intent_ids}
    hybrid_rankings = {intent_id: ["p1", "p2", "p3"] for intent_id in intent_ids}

    report = evaluate_ablation(
        dataset,
        backends={
            "graph": _backend(graph_rankings),
            "postgres": _backend(postgres_rankings),
            "hybrid": _backend(hybrid_rankings),
        },
        k=3,
    )

    assert report.dataset_id == dataset.id
    assert {variant.backend for variant in report.variants} == {
        "graph",
        "postgres",
        "hybrid",
    }
    assert all(
        {query.intent_id for query in variant.queries} == set(intent_ids)
        for variant in report.variants
    )
    assert all(
        {stage.stage for stage in query.stages} == {"retrieval", "final"}
        for variant in report.variants
        for query in variant.queries
    )
    assert report.best_backend(stage="final", metric="ndcg_at_k") == "hybrid"


def test_harness_rejects_missing_query_outputs() -> None:
    dataset = _dataset()

    def incomplete(
        case: RetrievalCase,
    ) -> tuple[RetrievalStageOutput, ...]:
        if case.id == dataset.intents[0].id:
            return ()
        return (RetrievalStageOutput(stage="final", hits=()),)

    with pytest.raises(ValueError, match="required ordered stages"):
        evaluate_ablation(dataset, backends={"broken": incomplete}, k=3)


def test_harness_rejects_selectively_omitted_required_stage() -> None:
    dataset = _dataset()

    def selective(case: RetrievalCase) -> tuple[RetrievalStageOutput, ...]:
        stages = [RetrievalStageOutput(stage="retrieval", hits=())]
        if case.id != dataset.intents[0].id:
            stages.append(RetrievalStageOutput(stage="final", hits=()))
        return tuple(stages)

    with pytest.raises(ValueError, match="required ordered stages"):
        evaluate_ablation(dataset, backends={"selective": selective}, k=3)


def test_harness_rejects_unknown_entities_from_backend() -> None:
    dataset = _dataset()

    def invalid(
        _case: RetrievalCase,
    ) -> tuple[RetrievalStageOutput, ...]:
        return (
            RetrievalStageOutput(
                stage="final",
                hits=(RankedHit(entity_id="not-in-world", score=1.0),),
            ),
        )

    with pytest.raises(ValueError, match="unknown entities"):
        evaluate_ablation(dataset, backends={"broken": invalid}, k=3)


def test_production_fusion_adapter_runs_real_hybrid_fusion_without_gold_facts() -> None:
    fixture = _dataset()
    dataset = fixture.model_copy(
        update={
            "intents": (fixture.intents[0],),
            "required_stages": ("final",),
        }
    )
    case_id = dataset.intents[0].id
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(
            BackendHit(candidate_id=99),  # stale/unscoped graph entity
            BackendHit(candidate_id=1),
        ),
    )
    postgres = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=2),),
    )
    inputs = {
        case_id: FusionEvalInput(
            allowed_applications={1: 101, 2: 102},
            entity_by_candidate_id={1: "p1", 2: "p2"},
            graph=graph,
            postgres=postgres,
        )
    }

    report = evaluate_ablation(
        dataset,
        backends={
            "postgres": production_fusion_retriever(
                inputs,
                mode=RetrievalMode.POSTGRES_ONLY,
            ),
            "graph": production_fusion_retriever(
                inputs,
                mode=RetrievalMode.GRAPH_ONLY,
            ),
            "hybrid": production_fusion_retriever(
                inputs,
                mode=RetrievalMode.HYBRID,
            ),
        },
        k=2,
    )
    aggregates = {
        variant.backend: variant.stage_aggregates[0] for variant in report.variants
    }

    assert aggregates["postgres"].recall_at_k == 0.0
    assert aggregates["graph"].recall_at_k == 1.0
    assert aggregates["hybrid"].recall_at_k == 1.0
    hybrid_hits = report.variants[2].queries[0].stages[0].metrics
    assert hybrid_hits.false_positive_count_at_k == 1


def test_production_fusion_adapter_preserves_backend_citation_evidence() -> None:
    fixture = _dataset()
    dataset = fixture.model_copy(
        update={
            "intents": (fixture.intents[0],),
            "required_stages": ("final",),
        }
    )
    intent = dataset.intents[0]
    criterion_id = intent.plan.criteria[0].id
    fact = next(fact for fact in dataset.world.facts if fact.id == "f-p1-k8s")
    citation = fact.provenance[0]
    reference = "episode:p1-k8s"
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(
            BackendHit(
                candidate_id=1,
                evidence=(
                    EvidenceHit(
                        source="cv",
                        reference=reference,
                        clause_ids=(criterion_id,),
                    ),
                ),
            ),
        ),
    )
    inputs = {
        intent.id: FusionEvalInput(
            allowed_applications={1: 101},
            entity_by_candidate_id={1: "p1"},
            graph=graph,
            citations_by_evidence={("cv", reference): (citation,)},
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
        k=1,
    )
    metrics = report.variants[0].queries[0].stages[0].metrics

    assert metrics.citation_count == 1
    assert metrics.supported_citation_count == 1
    assert metrics.grounded_hit_count == 1
