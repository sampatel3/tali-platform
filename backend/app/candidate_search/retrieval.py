"""Pure contracts and rank fusion for hybrid candidate retrieval.

PostgreSQL remains the authority for which candidate/application pairs are in
scope.  Graph and PostgreSQL retrievers may contribute recall and evidence, but
neither can place an unscoped candidate into the fused result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class RetrievalMode(str, Enum):
    """Backends selected by a retrieval plan."""

    POSTGRES_ONLY = "postgres_only"
    GRAPH_ONLY = "graph_only"
    HYBRID = "hybrid"


class BackendStatus(str, Enum):
    """Execution outcome for one retrieval backend."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class EvidenceHit:
    """Address of evidence returned by a backend for a candidate."""

    source: str
    reference: str
    clause_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("evidence source must not be empty")
        if not self.reference.strip():
            raise ValueError("evidence reference must not be empty")


@dataclass(frozen=True)
class BackendHit:
    """One backend's candidate hit; tuple position is its authoritative rank."""

    candidate_id: int
    evidence: tuple[EvidenceHit, ...] = ()
    raw_score: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.candidate_id, bool)
            or not isinstance(self.candidate_id, int)
            or self.candidate_id <= 0
        ):
            raise ValueError("candidate_id must be a positive integer")
        if self.raw_score is not None and not math.isfinite(self.raw_score):
            raise ValueError("raw_score must be finite")


@dataclass(frozen=True)
class BackendResult:
    """Ranked hits and coverage state reported by one backend."""

    backend: str
    status: BackendStatus
    hits: tuple[BackendHit, ...] = ()
    capped: bool = False
    exhaustive: bool = True
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.backend.strip():
            raise ValueError("backend must not be empty")
        if self.status is not BackendStatus.OK and self.hits:
            raise ValueError("a failed or unavailable backend cannot return hits")
        if self.status is not BackendStatus.OK and self.exhaustive:
            raise ValueError("a failed or unavailable backend cannot be exhaustive")
        if self.capped and self.exhaustive:
            raise ValueError("a capped backend result cannot be exhaustive")


@dataclass(frozen=True)
class FusedHit:
    """One authorized candidate after cross-backend rank fusion."""

    candidate_id: int
    application_id: int
    score: float
    graph_rank: int | None
    postgres_rank: int | None
    sources: tuple[str, ...]
    evidence: tuple[EvidenceHit, ...]


@dataclass(frozen=True)
class HybridResult:
    """Fused hits plus the backend states required to interpret an empty set."""

    mode: RetrievalMode
    hits: tuple[FusedHit, ...]
    graph: BackendResult | None
    postgres: BackendResult | None
    capped: bool
    exhaustive: bool

    @property
    def application_ids(self) -> tuple[int, ...]:
        return tuple(hit.application_id for hit in self.hits)

    @property
    def is_exact_empty(self) -> bool:
        """Whether every selected backend searched exhaustively and found none."""

        return not self.hits and self.exhaustive


def fuse_retrieval_results(
    *,
    mode: RetrievalMode,
    allowed_applications: Mapping[int, int],
    graph: BackendResult | None = None,
    postgres: BackendResult | None = None,
    graph_weight: float = 2.0,
    postgres_weight: float = 1.0,
    rrf_k: int = 60,
) -> HybridResult:
    """Fuse backend ranks after enforcing canonical PostgreSQL eligibility.

    ``allowed_applications`` must be built from the already tenant-, role-, and
    lifecycle-scoped PostgreSQL base query. Its keys are the only candidate IDs
    authorized to appear, and its values choose the application returned for
    each person.
    """

    if not isinstance(mode, RetrievalMode):
        raise ValueError("mode must be a RetrievalMode")
    if not math.isfinite(graph_weight) or graph_weight <= 0:
        raise ValueError("graph_weight must be finite and greater than zero")
    if not math.isfinite(postgres_weight) or postgres_weight <= 0:
        raise ValueError("postgres_weight must be finite and greater than zero")
    if isinstance(rrf_k, bool) or rrf_k < 0:
        raise ValueError("rrf_k must be a non-negative integer")

    selected = _selected_results(mode=mode, graph=graph, postgres=postgres)
    allowed = _validate_allowed_applications(allowed_applications)
    use_graph = mode in (RetrievalMode.GRAPH_ONLY, RetrievalMode.HYBRID)
    use_postgres = mode in (RetrievalMode.POSTGRES_ONLY, RetrievalMode.HYBRID)
    graph_hits = _ranked_hits(graph, allowed) if use_graph else {}
    postgres_hits = _ranked_hits(postgres, allowed) if use_postgres else {}

    fused: list[FusedHit] = []
    for candidate_id in graph_hits.keys() | postgres_hits.keys():
        graph_entry = graph_hits.get(candidate_id)
        postgres_entry = postgres_hits.get(candidate_id)
        graph_rank = graph_entry[0] if graph_entry else None
        postgres_rank = postgres_entry[0] if postgres_entry else None
        score = 0.0
        sources: list[str] = []
        evidence: list[EvidenceHit] = []
        if graph_entry is not None:
            score += graph_weight / (rrf_k + graph_rank)
            sources.append(graph.backend)  # type: ignore[union-attr]
            evidence.extend(graph_entry[1].evidence)
        if postgres_entry is not None:
            score += postgres_weight / (rrf_k + postgres_rank)
            sources.append(postgres.backend)  # type: ignore[union-attr]
            evidence.extend(postgres_entry[1].evidence)
        fused.append(
            FusedHit(
                candidate_id=candidate_id,
                application_id=allowed[candidate_id],
                score=score,
                graph_rank=graph_rank,
                postgres_rank=postgres_rank,
                sources=tuple(sources),
                evidence=tuple(evidence),
            )
        )

    fused.sort(
        key=lambda hit: (
            -hit.score,
            hit.graph_rank if hit.graph_rank is not None else math.inf,
            hit.postgres_rank if hit.postgres_rank is not None else math.inf,
            hit.candidate_id,
        )
    )
    capped = any(result.capped for result in selected)
    exhaustive = all(
        result.status is BackendStatus.OK and result.exhaustive and not result.capped
        for result in selected
    )
    return HybridResult(
        mode=mode,
        hits=tuple(fused),
        graph=graph,
        postgres=postgres,
        capped=capped,
        exhaustive=exhaustive,
    )


def _selected_results(
    *,
    mode: RetrievalMode,
    graph: BackendResult | None,
    postgres: BackendResult | None,
) -> tuple[BackendResult, ...]:
    if mode is RetrievalMode.POSTGRES_ONLY:
        if postgres is None:
            raise ValueError("postgres result is required in postgres_only mode")
        return (postgres,)
    if mode is RetrievalMode.GRAPH_ONLY:
        if graph is None:
            raise ValueError("graph result is required in graph_only mode")
        return (graph,)
    if graph is None or postgres is None:
        raise ValueError("graph and postgres results are required in hybrid mode")
    return (graph, postgres)


def _validate_allowed_applications(
    allowed_applications: Mapping[int, int],
) -> dict[int, int]:
    allowed: dict[int, int] = {}
    for candidate_id, application_id in allowed_applications.items():
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id <= 0
        ):
            raise ValueError("allowed candidate IDs must be positive integers")
        if (
            isinstance(application_id, bool)
            or not isinstance(application_id, int)
            or application_id <= 0
        ):
            raise ValueError("allowed application IDs must be positive integers")
        allowed[candidate_id] = application_id
    return allowed


def _ranked_hits(
    result: BackendResult | None,
    allowed: Mapping[int, int],
) -> dict[int, tuple[int, BackendHit]]:
    if result is None or result.status is not BackendStatus.OK:
        return {}
    ranked: dict[int, tuple[int, BackendHit]] = {}
    for rank, hit in enumerate(result.hits, start=1):
        if hit.candidate_id in allowed and hit.candidate_id not in ranked:
            ranked[hit.candidate_id] = (rank, hit)
    return ranked
