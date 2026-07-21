"""Eval adapter around the production hybrid rank-fusion primitive."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping

from ..retrieval import (
    BackendResult,
    BackendStatus,
    RetrievalMode,
    fuse_retrieval_results,
)
from .contracts import (
    Citation,
    CriterionEvidence,
    RankedHit,
    ResultStatus,
    RetrievalCase,
    RetrievalStageOutput,
)
from .harness import BackendRetriever


@dataclass(frozen=True)
class FusionEvalInput:
    """Backend outputs visible to fusion, with no oracle facts or gold plan."""

    allowed_applications: Mapping[int, int]
    entity_by_candidate_id: Mapping[int, str]
    graph: BackendResult | None = None
    postgres: BackendResult | None = None
    # Production fusion carries source/reference/clause identities, while the
    # eval metric requires immutable spans.  The backend adapter supplies the
    # spans it actually retrieved, keyed by that source identity; the oracle is
    # never consulted here.
    citations_by_evidence: Mapping[
        tuple[str, str], tuple[Citation, ...]
    ] = field(default_factory=dict)
    verified_absent_by_candidate_id: Mapping[
        int, tuple[str, ...]
    ] = field(default_factory=dict)


def _criterion_evidence(
    fused_evidence,
    citations_by_evidence: Mapping[tuple[str, str], tuple[Citation, ...]],
) -> tuple[CriterionEvidence, ...]:
    grouped: dict[str, list[Citation]] = defaultdict(list)
    for item in fused_evidence:
        citations = citations_by_evidence.get((item.source, item.reference), ())
        if not citations:
            continue
        for criterion_id in item.clause_ids:
            grouped[criterion_id].extend(citations)
    return tuple(
        CriterionEvidence(
            criterion_id=criterion_id,
            citations=tuple(dict.fromkeys(citations)),
        )
        for criterion_id, citations in grouped.items()
    )


def _combined_status(
    mode: RetrievalMode,
    graph: BackendResult | None,
    postgres: BackendResult | None,
) -> ResultStatus:
    selected: tuple[BackendResult | None, ...]
    if mode is RetrievalMode.GRAPH_ONLY:
        selected = (graph,)
    elif mode is RetrievalMode.POSTGRES_ONLY:
        selected = (postgres,)
    else:
        selected = (graph, postgres)
    results = tuple(result for result in selected if result is not None)
    if results and all(result.status is BackendStatus.OK for result in results):
        return ResultStatus.OK
    if any(result.status is BackendStatus.OK for result in results):
        return ResultStatus.PARTIAL
    if any(result.status is BackendStatus.ERROR for result in results):
        return ResultStatus.ERROR
    return ResultStatus.UNAVAILABLE


def production_fusion_retriever(
    cases: Mapping[str, FusionEvalInput],
    *,
    mode: RetrievalMode,
    stage: str = "final",
) -> BackendRetriever:
    """Build a deterministic harness backend that executes production fusion."""

    def retrieve(case: RetrievalCase) -> tuple[RetrievalStageOutput, ...]:
        inputs = cases[case.id]
        result = fuse_retrieval_results(
            mode=mode,
            allowed_applications=inputs.allowed_applications,
            graph=inputs.graph,
            postgres=inputs.postgres,
        )
        status = _combined_status(mode, inputs.graph, inputs.postgres)
        hits = tuple(
            RankedHit(
                entity_id=inputs.entity_by_candidate_id[hit.candidate_id],
                score=hit.score,
                evidence=_criterion_evidence(
                    hit.evidence,
                    inputs.citations_by_evidence,
                ),
                verified_absent_criterion_ids=tuple(
                    inputs.verified_absent_by_candidate_id.get(hit.candidate_id, ())
                ),
            )
            for hit in result.hits
        )
        return (
            RetrievalStageOutput(
                stage=stage,
                hits=hits,
                status=status,
                capped=result.capped,
                exhaustive=result.exhaustive,
            ),
        )

    return retrieve


__all__ = ["FusionEvalInput", "production_fusion_retriever"]
