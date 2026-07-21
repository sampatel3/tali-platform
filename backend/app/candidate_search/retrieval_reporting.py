"""Public retrieval trace and degraded-state reporting."""

from __future__ import annotations

from typing import Any

from .retrieval import BackendStatus, HybridResult
from .schemas import (
    SearchRetrievalSummary,
    SearchRetrievalTrace,
    SearchWarning,
)


def retrieval_summary(
    result: HybridResult,
    graph_coverage: float | None,
) -> SearchRetrievalSummary:
    graph_status = result.graph.status.value if result.graph else "not_selected"
    return SearchRetrievalSummary(
        mode=result.mode.value,
        graph_status=graph_status,
        graph_coverage=graph_coverage,
        capped=result.capped,
        exhaustive=result.exhaustive,
        is_exact_empty=result.is_exact_empty,
        hits=[
            SearchRetrievalTrace(
                application_id=hit.application_id,
                candidate_id=hit.candidate_id,
                score=hit.score,
                sources=list(hit.sources),
                graph_rank=hit.graph_rank,
                postgres_rank=hit.postgres_rank,
                evidence=[
                    {
                        "source": item.source,
                        "reference": item.reference,
                        "clause_ids": list(item.clause_ids),
                    }
                    for item in hit.evidence
                ],
            )
            for hit in result.hits
        ],
    )


def append_retrieval_warnings(
    warnings: list[SearchWarning],
    result: HybridResult,
    graph_coverage: float | None,
) -> None:
    graph = result.graph
    if graph is None:
        return
    if graph.status is BackendStatus.UNAVAILABLE:
        code = "graph_retrieval_unavailable"
        message = (
            "Graph recall is unavailable; PostgreSQL results were retained "
            "and the search is not exhaustive."
        )
    elif graph.status is BackendStatus.ERROR:
        code = "graph_retrieval_failed"
        message = (
            "Graph recall failed; PostgreSQL results were retained and the "
            "search is not exhaustive."
        )
    elif not graph.exhaustive:
        coverage = (
            f"{graph_coverage:.0%}" if graph_coverage is not None else "unknown"
        )
        code = "graph_coverage_partial"
        message = (
            f"Graph recall coverage is {coverage}; retrieval candidates require "
            "source grounding and an empty result is not exact."
        )
    else:
        return
    warnings.append(SearchWarning(code=code, message=message))  # type: ignore[arg-type]


def page_retrieval_payload(
    payload: dict[str, Any],
    *,
    eligible_application_ids: list[int],
    page_application_ids: list[int],
    retrieval_matches: int,
) -> dict[str, Any]:
    """Restrict public retrieval traces to one authorized result page."""

    raw_hits = list(payload.get("hits") or [])
    eligible_ids = set(eligible_application_ids)
    eligible_hits = [
        hit
        for hit in raw_hits
        if int(hit.get("application_id") or 0) in eligible_ids
    ]
    hit_by_application_id = {
        int(hit["application_id"]): hit
        for hit in eligible_hits
        if int(hit.get("application_id") or 0) > 0
    }
    page_hits = [
        hit_by_application_id[application_id]
        for application_id in page_application_ids
        if application_id in hit_by_application_id
    ]
    return {
        **payload,
        "total_hits": max(len(raw_hits), int(retrieval_matches)),
        "filtered_hits": len(eligible_hits),
        "returned_hits": len(page_hits),
        "hits": page_hits,
    }


def search_output_metadata(result: Any, *, retrieval_matches: int) -> dict[str, Any]:
    """Shared public coverage fields for shortlist-style search responses."""

    return {
        "search_plan": result.search_plan,
        "retrieval": (
            result.retrieval.model_dump(mode="json")
            if result.retrieval is not None
            else None
        ),
        "database_matches": result.database_matches,
        "retrieval_matches": retrieval_matches,
        "postgres_matches": result.database_matches,
        "is_exact_empty": result.is_exact_empty,
        "exhaustive": bool(result.exhaustive),
    }


__all__ = [
    "append_retrieval_warnings",
    "page_retrieval_payload",
    "retrieval_summary",
    "search_output_metadata",
]
