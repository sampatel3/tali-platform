"""Offline harness for graph/Postgres/hybrid backend ablations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence

from .contracts import (
    BackendAblationReport,
    BackendVariantEvaluation,
    ConstructedDataset,
    QueryEvaluation,
    RetrievalCase,
    RetrievalStageOutput,
    StageAggregate,
    StageEvaluation,
    StageMetrics,
)
from .metrics import evaluate_stage
from .oracle import derive_judgments

BackendRetriever = Callable[[RetrievalCase], Sequence[RetrievalStageOutput]]


def _mean(rows: Sequence[StageMetrics], field: str) -> float:
    return sum(float(getattr(row, field)) for row in rows) / len(rows)


def _aggregate(
    metrics_by_stage: Mapping[str, Sequence[StageMetrics]],
) -> tuple[StageAggregate, ...]:
    aggregates: list[StageAggregate] = []
    for stage, rows in metrics_by_stage.items():
        aggregates.append(
            StageAggregate(
                stage=stage,
                query_count=len(rows),
                precision_at_k=_mean(rows, "precision_at_k"),
                recall_at_k=_mean(rows, "recall_at_k"),
                mrr=_mean(rows, "mrr"),
                ndcg_at_k=_mean(rows, "ndcg_at_k"),
                false_positive_count_at_k=sum(
                    row.false_positive_count_at_k for row in rows
                ),
                exact_empty_accuracy=_mean(rows, "exact_empty_accuracy"),
                citation_span_validity=_mean(rows, "citation_span_validity"),
                citation_support_validity=_mean(
                    rows, "citation_support_validity"
                ),
                citation_count=sum(row.citation_count for row in rows),
                valid_citation_count=sum(row.valid_citation_count for row in rows),
                supported_citation_count=sum(
                    row.supported_citation_count for row in rows
                ),
                grounded_hit_coverage=_mean(rows, "grounded_hit_coverage"),
                grounded_hit_count=sum(row.grounded_hit_count for row in rows),
            )
        )
    return tuple(aggregates)


def evaluate_ablation(
    dataset: ConstructedDataset,
    *,
    backends: Mapping[str, BackendRetriever],
    k: int = 10,
) -> BackendAblationReport:
    """Evaluate each backend on the same world and independently derived truth."""

    if not backends:
        raise ValueError("at least one backend is required")
    variants: list[BackendVariantEvaluation] = []
    for backend, retrieve in backends.items():
        query_results: list[QueryEvaluation] = []
        metrics_by_stage: dict[str, list[StageMetrics]] = defaultdict(list)
        for intent in dataset.intents:
            judgments = derive_judgments(dataset.world, intent)
            case = RetrievalCase(
                id=intent.id,
                query=intent.plan.query,
                corpus=dataset.retrieval_corpus,
            )
            stages = tuple(retrieve(case))
            known_entities = {entity.id for entity in dataset.world.entities}
            for stage in stages:
                unknown = sorted(
                    {hit.entity_id for hit in stage.hits} - known_entities
                )
                if unknown:
                    raise ValueError(
                        f"backend {backend!r} returned unknown entities "
                        f"for intent {intent.id!r}: {unknown}"
                    )
            names = [stage.stage for stage in stages]
            if len(names) != len(set(names)):
                raise ValueError(
                    f"backend {backend!r} returned duplicate stage names "
                    f"for intent {intent.id!r}"
                )
            if tuple(names) != dataset.required_stages:
                raise ValueError(
                    f"backend {backend!r} returned stages {tuple(names)!r} "
                    f"for intent {intent.id!r}; required ordered stages are "
                    f"{dataset.required_stages!r}"
                )
            stage_results: list[StageEvaluation] = []
            for stage in stages:
                metrics = evaluate_stage(
                    stage,
                    judgments,
                    plan=intent.plan,
                    documents=dataset.world.documents,
                    k=k,
                )
                metrics_by_stage[stage.stage].append(metrics)
                stage_results.append(
                    StageEvaluation(stage=stage.stage, metrics=metrics)
                )
            query_results.append(
                QueryEvaluation(
                    intent_id=intent.id,
                    judgments=judgments,
                    stages=tuple(stage_results),
                )
            )
        variants.append(
            BackendVariantEvaluation(
                backend=backend,
                queries=tuple(query_results),
                stage_aggregates=_aggregate(metrics_by_stage),
            )
        )
    return BackendAblationReport(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        k=k,
        variants=tuple(variants),
    )


__all__ = ["BackendRetriever", "evaluate_ablation"]
