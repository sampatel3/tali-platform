"""PostgreSQL authority helpers for hybrid candidate retrieval.

The graph is allowed to improve recall, never tenancy or lifecycle scope.  A
population filter therefore keeps deterministic constraints while relaxing
only fields whose truth must be proven from source evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session, aliased

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.graph_sync_state import GraphSyncState
from .plan_evidence import evidence_scoped_structured_fields
from .schemas import ParsedFilter

logger = logging.getLogger("taali.candidate_search.population")

_SEARCHABLE_CANDIDATE = aliased(
    Candidate,
    name="searchable_candidate_lifecycle",
)
_SEARCHABLE_SCOPE_EXECUTION_OPTION = "_taali_searchable_candidate_scoped"


def apply_live_candidate_scope(
    base_query,
    *,
    organization_id: int,
    candidate_entity=Candidate,
):
    """Require a live person row inside one organization.

    Direct candidate routes and application-backed search surfaces share this
    lifecycle boundary.  Keeping it here prevents a detail/download endpoint
    from accidentally treating a soft-deleted person as readable merely
    because an application or document row still exists for audit purposes.
    """

    return base_query.filter(
        candidate_entity.organization_id == int(organization_id),
        candidate_entity.deleted_at.is_(None),
    )


def lock_live_candidate_for_execution(
    db: Session,
    *,
    organization_id: int,
    candidate_id: int,
) -> Candidate | None:
    """Lock one live person through an irreversible execution boundary.

    Delayed email and ATS workers must re-authorize the person after dequeue,
    not rely on the lifecycle snapshot captured when work was enqueued. On
    PostgreSQL the row lock also serializes a concurrent erasure until the
    caller commits or rolls back its provider result.
    """

    query = db.query(Candidate).filter(
        Candidate.id == int(candidate_id),
        Candidate.organization_id == int(organization_id),
        Candidate.deleted_at.is_(None),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(of=Candidate)
    return query.populate_existing().one_or_none()


def apply_searchable_candidate_scope(
    base_query,
    *,
    organization_id: int,
):
    """Exclude erased/cross-tenant person rows at the shared read boundary.

    A named alias avoids colliding with callers that separately join
    ``Candidate`` for text filters. The indexed candidate PK join is also
    intentionally flat: this scope is embedded in already-rich logical-role
    score queries, where another correlated subquery would materially increase
    SQLite planning time without improving PostgreSQL authority.
    """

    get_options = getattr(base_query, "get_execution_options", None)
    existing_organization_id = (
        get_options().get(_SEARCHABLE_SCOPE_EXECUTION_OPTION)
        if callable(get_options)
        else None
    )
    if existing_organization_id is not None:
        if int(existing_organization_id) != int(organization_id):
            # A layered reader must never replace an established tenant scope.
            return base_query.filter(False)
        return base_query
    scoped = base_query.join(
        _SEARCHABLE_CANDIDATE,
        _SEARCHABLE_CANDIDATE.id == CandidateApplication.candidate_id,
    )
    scoped = apply_live_candidate_scope(
        scoped,
        organization_id=int(organization_id),
        candidate_entity=_SEARCHABLE_CANDIDATE,
    )
    return scoped.execution_options(
        **{_SEARCHABLE_SCOPE_EXECUTION_OPTION: int(organization_id)},
    )


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
    "apply_live_candidate_scope",
    "apply_searchable_candidate_scope",
    "application_map_from_rows",
    "estimate_graph_coverage",
    "lock_live_candidate_for_execution",
    "population_filter",
]
