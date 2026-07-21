"""PostgreSQL authority helpers for hybrid candidate retrieval.

The graph is allowed to improve recall, never tenancy or lifecycle scope.  A
population filter therefore keeps deterministic constraints while relaxing
only fields whose truth must be proven from source evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import exists

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.graph_sync_state import GraphSyncState
from .plan_evidence import evidence_scoped_structured_fields
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.population")


def apply_searchable_candidate_scope(base_query, *, organization_id: int):
    """Exclude erased/cross-tenant person rows at the shared search boundary."""

    searchable_candidate = exists().where(
        Candidate.id == CandidateApplication.candidate_id,
        Candidate.organization_id == int(organization_id),
        Candidate.deleted_at.is_(None),
    )
    return base_query.filter(searchable_candidate)


def population_filter(parsed: ParsedFilter) -> ParsedFilter:
    """Return deterministic constraints safe for the authorization pool.

    Qualitative claims, graph relationships, and residual keywords are recall
    or evidence concerns.  They must not shrink the canonical PostgreSQL pool
    before GraphDB has a chance to recover a valid candidate.
    """

    relaxed = evidence_scoped_structured_fields(parsed)
    updates: dict[str, Any] = {
        "graph_predicates": [],
        "soft_criteria": [],
        "preferred_criteria": [],
        "keywords": [],
    }
    for field_name in relaxed:
        if field_name == "min_years_experience":
            updates[field_name] = None
        elif field_name in ParsedFilter.model_fields:
            updates[field_name] = []
    return parsed.model_copy(update=updates)


def application_map_from_rows(rows: Iterable[object]) -> dict[int, int]:
    """Choose the first scoped application for each candidate/person."""

    selected: dict[int, int] = {}
    for row in rows:
        application_id, candidate_id = _row_ids(row)
        if application_id is None or candidate_id is None:
            continue
        if application_id <= 0 or candidate_id <= 0:
            continue
        selected.setdefault(candidate_id, application_id)
    return selected


def estimate_graph_coverage(
    db,
    candidate_ids: Sequence[int] | Iterable[int],
) -> float | None:
    """Estimate indexed-pool coverage from current GraphSyncState rows.

    This ratio is observability, not a completeness watermark: sync state does
    not prove that every later note/event is indexed.  Hybrid execution must
    keep its separate ``coverage_authoritative`` flag false for this estimate.
    A read failure returns ``None`` rather than inventing zero coverage.
    """

    scoped_ids = {
        int(candidate_id)
        for candidate_id in candidate_ids
        if candidate_id is not None and int(candidate_id) > 0
    }
    if not scoped_ids:
        return 1.0
    try:
        rows = (
            db.query(GraphSyncState.candidate_id)
            .filter(
                GraphSyncState.candidate_id.in_(scoped_ids),
                GraphSyncState.content_hash.is_not(None),
            )
            .all()
        )
    except Exception as exc:  # coverage is diagnostic, never search-blocking
        logger.debug("Graph coverage estimate unavailable: %s", exc)
        return None
    synced = {
        int(row[0])
        for row in rows
        if row and row[0] is not None and int(row[0]) in scoped_ids
    }
    return len(synced) / len(scoped_ids)


def _row_ids(row: object) -> tuple[int | None, int | None]:
    mapping: Mapping[str, Any] | None = None
    if isinstance(row, Mapping):
        mapping = row
    else:
        candidate_mapping = getattr(row, "_mapping", None)
        if isinstance(candidate_mapping, Mapping):
            mapping = candidate_mapping
    if mapping is not None:
        return _positive_int(mapping.get("application_id") or mapping.get("id")), _positive_int(
            mapping.get("candidate_id")
        )
    if hasattr(row, "application_id") or hasattr(row, "candidate_id"):
        return _positive_int(
            getattr(row, "application_id", getattr(row, "id", None))
        ), _positive_int(getattr(row, "candidate_id", None))
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) >= 2:
        return _positive_int(row[0]), _positive_int(row[1])
    raise ValueError("application row must expose application and candidate IDs")


def _positive_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


__all__ = [
    "apply_searchable_candidate_scope",
    "application_map_from_rows",
    "estimate_graph_coverage",
    "population_filter",
]
