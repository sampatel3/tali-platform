"""Graph-first candidate recall with PostgreSQL authorization and rank fusion.

This module is deliberately orchestration-only.  Callers build the canonical
candidate-to-application map from their already tenant-, role-, lifecycle-,
and deletion-scoped PostgreSQL query.  Neither graph retrieval nor a ranked
PostgreSQL retriever can add a candidate outside that map.

Graphiti facts are generated retrieval context, not citation sources.  Only
hydrated original episode content is converted to an ``EvidenceHit``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..candidate_graph.search import (
    DEFAULT_SEARCH_LIMIT,
    GraphCandidateEvidenceHit,
    GraphEvidenceSearchResult,
)
from .evidence_matching import contains_grounding_value
from .graph_retrieval_cache import (
    GraphRetrievalCacheKey,
    graph_retrieval_cache,
    validate_search_query,
)
from .retrieval import (
    BackendHit,
    BackendResult,
    BackendStatus,
    EvidenceHit,
    HybridResult,
    RetrievalMode,
    fuse_retrieval_results,
)

logger = logging.getLogger("taali.candidate_search.hybrid")

GraphSearchFn = Callable[..., GraphEvidenceSearchResult]
PostgresInput = BackendResult | Iterable[BackendHit | Mapping[str, Any] | object]


@dataclass(frozen=True, slots=True)
class GraphEvidenceClause:
    """A value that must be present in original graph episode evidence."""

    clause_id: str
    value: str
    predicate: str | None = None

    def __post_init__(self) -> None:
        if not self.clause_id.strip() or not self.value.strip():
            raise ValueError("graph evidence clause id and value are required")


@dataclass(frozen=True, slots=True)
class GraphEvidenceRequirement:
    """Boolean evidence group used to validate graph recall locally."""

    operator: str
    clauses: tuple[GraphEvidenceClause, ...]

    def __post_init__(self) -> None:
        if self.operator not in {"all", "any"}:
            raise ValueError("graph evidence operator must be all or any")
        if not self.clauses:
            raise ValueError("graph evidence requirement needs at least one clause")


def run_hybrid_retrieval(
    *,
    query: str,
    organization_id: int,
    allowed_applications: Mapping[int, int],
    postgres: PostgresInput | None = None,
    role_id: int | None = None,
    graph_search_fn: GraphSearchFn | None = None,
    graph_result: BackendResult | None = None,
    graph_coverage: float | None = None,
    graph_coverage_authoritative: bool = False,
    graph_clause_ids: Iterable[str] = (),
    graph_requirements: Iterable[GraphEvidenceRequirement] = (),
    graph_limit: int = DEFAULT_SEARCH_LIMIT,
    mode: RetrievalMode = RetrievalMode.HYBRID,
    graph_weight: float = 2.0,
    postgres_weight: float = 1.0,
    rrf_k: int = 60,
) -> HybridResult:
    """Retrieve and fuse ranked candidate hits without weakening SQL scope.

    In hybrid mode the raw user query is sent to ``graph_search_fn`` exactly
    once.  The injectable function makes the orchestration independently
    testable without Graphiti, embeddings, or model calls.

    ``graph_coverage`` is the fraction of the canonical eligible pool known to
    be indexed in the graph.  A successful graph query is treated as
    non-exhaustive unless coverage is explicitly ``1.0`` *and* the caller marks
    that measurement authoritative.  Ordinary sync telemetry is not a
    completeness watermark and therefore cannot certify an exact zero.

    ``postgres`` may be a pre-built ``BackendResult`` or ranked row-like values
    exposing ``candidate_id`` (as an attribute or mapping key).  ``BackendHit``
    values are accepted directly.
    """
    _validate_scope_inputs(
        query=query,
        organization_id=organization_id,
        role_id=role_id,
        graph_coverage=graph_coverage,
        graph_coverage_authoritative=graph_coverage_authoritative,
        graph_limit=graph_limit,
    )
    if not isinstance(mode, RetrievalMode):
        raise ValueError("mode must be a RetrievalMode")

    postgres_result = postgres_backend_result(postgres)
    normalized_requirements = tuple(graph_requirements)
    selected_graph = graph_result
    if mode in (RetrievalMode.GRAPH_ONLY, RetrievalMode.HYBRID):
        if selected_graph is None:
            selected_graph = retrieve_graph_backend(
                query=query,
                organization_id=organization_id,
                role_id=role_id,
                graph_search_fn=graph_search_fn,
                graph_coverage=graph_coverage,
                graph_coverage_authoritative=graph_coverage_authoritative,
                graph_clause_ids=graph_clause_ids,
                graph_requirements=normalized_requirements,
                graph_limit=graph_limit,
            )

    return fuse_retrieval_results(
        mode=mode,
        allowed_applications=allowed_applications,
        graph=selected_graph,
        postgres=postgres_result,
        graph_weight=graph_weight,
        postgres_weight=postgres_weight,
        rrf_k=rrf_k,
    )


def retrieve_graph_backend(
    *,
    query: str,
    organization_id: int,
    role_id: int | None = None,
    graph_search_fn: GraphSearchFn | None = None,
    graph_coverage: float | None = None,
    graph_coverage_authoritative: bool = False,
    graph_clause_ids: Iterable[str] = (),
    graph_requirements: Iterable[GraphEvidenceRequirement] = (),
    graph_limit: int = DEFAULT_SEARCH_LIMIT,
) -> BackendResult:
    """Execute one graph recall pass before PostgreSQL re-authorization."""

    normalized_query = validate_search_query(query)
    search_kwargs = {
        "organization_id": organization_id,
        "role_id": role_id,
        "queries": (normalized_query,),
        "limit_per_query": graph_limit,
    }
    try:
        if graph_search_fn is None:
            cache_key = GraphRetrievalCacheKey(
                organization_id=organization_id,
                role_id=role_id,
                query=" ".join(normalized_query.casefold().split()),
                limit=graph_limit,
            )
            raw_result = graph_retrieval_cache.get_or_load(
                cache_key,
                lambda: _default_graph_search(**search_kwargs),
            )
        else:
            # Injected implementations are test/caller-controlled and must not
            # read or contaminate the production process cache.
            raw_result = graph_search_fn(**search_kwargs)
        return graph_backend_result(
            raw_result,
            graph_coverage=graph_coverage,
            graph_coverage_authoritative=graph_coverage_authoritative,
            clause_ids=tuple(graph_clause_ids),
            requirements=tuple(graph_requirements),
        )
    except Exception as exc:
        logger.warning("Hybrid graph retrieval failed: %s", exc)
        return BackendResult(
            backend="graph",
            status=BackendStatus.ERROR,
            exhaustive=False,
            error_code="graph_search_error",
        )


def clear_graph_retrieval_cache() -> None:
    """Test/operations seam for clearing completed process-local entries."""

    graph_retrieval_cache.clear()


def postgres_backend_result(postgres: PostgresInput | None) -> BackendResult:
    """Normalize ranked PostgreSQL rows without changing a supplied result."""
    if isinstance(postgres, BackendResult):
        return postgres
    if postgres is None:
        values: Iterable[BackendHit | Mapping[str, Any] | object] = ()
    else:
        values = postgres

    return BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=tuple(_postgres_hit(value) for value in values),
    )


def graph_backend_result(
    result: GraphEvidenceSearchResult,
    *,
    graph_coverage: float | None,
    graph_coverage_authoritative: bool = False,
    clause_ids: tuple[str, ...] = (),
    requirements: tuple[GraphEvidenceRequirement, ...] = (),
) -> BackendResult:
    """Convert graph evidence while retaining only citeable episode sources."""
    status = _backend_status(result.status)
    if status is not BackendStatus.OK:
        # Partial graph results have uncertain recall and can have uncertain
        # ranking.  Drop them rather than silently presenting a partial success.
        return BackendResult(
            backend="graph",
            status=status,
            capped=result.capped,
            exhaustive=False,
            error_code=(
                "graph_unavailable"
                if status is BackendStatus.UNAVAILABLE
                else "graph_search_error"
            ),
        )

    hits = _graph_backend_hits(
        result.hits,
        clause_ids=clause_ids,
        requirements=requirements,
    )
    fully_indexed = (
        graph_coverage_authoritative
        and graph_coverage is not None
        and graph_coverage == 1.0
    )
    exhaustive = result.exhaustive and fully_indexed and not result.capped
    return BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=hits,
        capped=result.capped,
        exhaustive=exhaustive,
    )


def _default_graph_search(**kwargs: Any) -> GraphEvidenceSearchResult:
    # Resolve lazily so importing this pure orchestrator cannot initiate graph
    # work and tests can inject a zero-cost local implementation.
    from ..candidate_graph.search import search_candidate_evidence

    return search_candidate_evidence(**kwargs)


def _postgres_hit(value: BackendHit | Mapping[str, Any] | object) -> BackendHit:
    if isinstance(value, BackendHit):
        return value

    mapping: Mapping[str, Any] | None = None
    if isinstance(value, Mapping):
        mapping = value
    else:
        row_mapping = getattr(value, "_mapping", None)
        if isinstance(row_mapping, Mapping):
            mapping = row_mapping

    if mapping is not None:
        if "candidate_id" not in mapping:
            raise ValueError("PostgreSQL row must expose candidate_id")
        candidate_id = mapping["candidate_id"]
        evidence = mapping.get("evidence", ())
        raw_score = mapping.get("raw_score")
    elif hasattr(value, "candidate_id"):
        candidate_id = getattr(value, "candidate_id")
        evidence = getattr(value, "evidence", ())
        raw_score = getattr(value, "raw_score", None)
    else:
        raise ValueError("PostgreSQL row must expose candidate_id")

    return BackendHit(
        candidate_id=candidate_id,
        evidence=tuple(evidence),
        raw_score=raw_score,
    )


def _graph_backend_hits(
    values: Iterable[GraphCandidateEvidenceHit],
    *,
    clause_ids: tuple[str, ...],
    requirements: tuple[GraphEvidenceRequirement, ...],
) -> tuple[BackendHit, ...]:
    # The graph adapter provides query/rank explicitly.  Sorting protects that
    # authority if hydration returns records in a different order.
    ranked = sorted(
        enumerate(values),
        key=lambda item: (item[1].query_index, item[1].rank, item[0]),
    )
    qualified: list[tuple[GraphCandidateEvidenceHit, tuple[EvidenceHit, ...]]] = []
    content_by_candidate: dict[int, dict[tuple[str, str], str]] = {}
    for _, value in ranked:
        evidence, contents = _episode_evidence(value, clause_ids=clause_ids)
        if evidence:
            qualified.append((value, evidence))
            content_by_candidate.setdefault(value.candidate_id, {}).update(contents)

    # Fusion retains the first occurrence of each candidate, so aggregate all
    # of that candidate's original sources onto its first qualified hit while
    # keeping duplicate hit positions.  That preserves the backend's rank
    # sequence for later candidates.
    all_evidence: dict[int, tuple[EvidenceHit, ...]] = {}
    for value, evidence in qualified:
        all_evidence[value.candidate_id] = _merge_evidence(
            all_evidence.get(value.candidate_id, ()), evidence
        )

    ordered: list[BackendHit] = []
    seen_candidates: set[int] = set()
    for value, evidence in qualified:
        if value.candidate_id not in seen_candidates:
            evidence = all_evidence[value.candidate_id]
            seen_candidates.add(value.candidate_id)
        ordered.append(BackendHit(candidate_id=value.candidate_id, evidence=evidence))
    if not requirements:
        return tuple(ordered)
    return tuple(
        grounded
        for hit in ordered
        if (
            grounded := _ground_graph_hit(
                hit,
                requirements,
                content_by_candidate.get(hit.candidate_id, {}),
            )
        )
        is not None
    )


def _ground_graph_hit(
    hit: BackendHit,
    requirements: tuple[GraphEvidenceRequirement, ...],
    contents: Mapping[tuple[str, str], str],
) -> BackendHit | None:
    matched_evidence: dict[tuple[str, str], set[str]] = {}
    for requirement in requirements:
        matched_clauses: set[str] = set()
        for clause in requirement.clauses:
            for evidence in hit.evidence:
                content = contents.get((evidence.source, evidence.reference), "")
                if contains_grounding_value(
                    content,
                    clause.value,
                    predicate=clause.predicate,
                ):
                    matched_clauses.add(clause.clause_id)
                    matched_evidence.setdefault(
                        (evidence.source, evidence.reference), set()
                    ).add(clause.clause_id)
        satisfied = (
            len(matched_clauses) == len(requirement.clauses)
            if requirement.operator == "all"
            else bool(matched_clauses)
        )
        if not satisfied:
            return None
    evidence = tuple(
        EvidenceHit(source=source, reference=reference, clause_ids=tuple(sorted(ids)))
        for (source, reference), ids in matched_evidence.items()
    )
    return BackendHit(candidate_id=hit.candidate_id, evidence=evidence)


def _episode_evidence(
    hit: GraphCandidateEvidenceHit,
    *,
    clause_ids: tuple[str, ...],
) -> tuple[tuple[EvidenceHit, ...], dict[tuple[str, str], str]]:
    evidence: list[EvidenceHit] = []
    contents: dict[tuple[str, str], str] = {}
    seen: set[tuple[str, str]] = set()
    for episode in hit.episodes:
        uuid = str(episode.uuid).strip()
        content = str(episode.content).strip()
        if not uuid or not content:
            continue
        source_description = (episode.source_description or "").strip()
        source = source_description or "graph_episode"
        reference = f"episode:{uuid}"
        identity = (source, reference)
        if identity in seen:
            continue
        seen.add(identity)
        contents[identity] = content
        evidence.append(
            EvidenceHit(
                source=source,
                reference=reference,
                clause_ids=clause_ids,
            )
        )
    return tuple(evidence), contents


def _merge_evidence(
    first: tuple[EvidenceHit, ...],
    second: tuple[EvidenceHit, ...],
) -> tuple[EvidenceHit, ...]:
    merged: list[EvidenceHit] = []
    seen: set[EvidenceHit] = set()
    for item in (*first, *second):
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return tuple(merged)


def _backend_status(value: str) -> BackendStatus:
    try:
        return BackendStatus(value)
    except ValueError as exc:
        raise ValueError(f"unknown graph backend status: {value}") from exc


def _validate_scope_inputs(
    *,
    query: str,
    organization_id: int,
    role_id: int | None,
    graph_coverage: float | None,
    graph_coverage_authoritative: bool,
    graph_limit: int,
) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if (
        isinstance(organization_id, bool)
        or not isinstance(organization_id, int)
        or organization_id <= 0
    ):
        raise ValueError("organization_id must be a positive integer")
    if role_id is not None and (
        isinstance(role_id, bool) or not isinstance(role_id, int) or role_id <= 0
    ):
        raise ValueError("role_id must be a positive integer when provided")
    if (
        isinstance(graph_limit, bool)
        or not isinstance(graph_limit, int)
        or graph_limit < 1
    ):
        raise ValueError("graph_limit must be a positive integer")
    if graph_coverage is not None and (
        isinstance(graph_coverage, bool)
        or not isinstance(graph_coverage, (int, float))
        or not math.isfinite(float(graph_coverage))
        or not 0.0 <= float(graph_coverage) <= 1.0
    ):
        raise ValueError("graph_coverage must be between 0.0 and 1.0")
    if not isinstance(graph_coverage_authoritative, bool):
        raise ValueError("graph_coverage_authoritative must be a boolean")
