"""Bounded, relevance-preserving person retrieval from application rows."""

from __future__ import annotations

from collections.abc import Sequence


APPLICATION_ROWS_PER_PERSON_BUDGET = 5
MAX_PERSON_RETRIEVAL_LIMIT = 1000
MAX_APPLICATION_ROW_WINDOW = (
    MAX_PERSON_RETRIEVAL_LIMIT * APPLICATION_ROWS_PER_PERSON_BUDGET
)


def bounded_person_rows(
    query,
    *,
    application_id_column,
    candidate_id_column,
    person_limit: int,
) -> tuple[list[tuple[int, int]], bool]:
    """Return the first ranked application per person within a bounded window.

    Application rows are intentionally over-fetched because one person can have
    applications for several roles.  A full raw-row window is conservatively
    marked capped: it does not prove that another person is absent beyond the
    window.  Callers can therefore distinguish a complete small result from a
    bounded partial result without issuing an unbounded query.
    """

    safe_limit = max(1, min(int(person_limit), MAX_PERSON_RETRIEVAL_LIMIT))
    raw_limit = min(
        safe_limit * APPLICATION_ROWS_PER_PERSON_BUDGET + 1,
        MAX_APPLICATION_ROW_WINDOW + 1,
    )
    fetched = list(
        query.with_entities(application_id_column, candidate_id_column)
        .limit(raw_limit)
        .all()
    )[:raw_limit]

    selected: list[tuple[int, int]] = []
    seen_candidates: set[int] = set()
    has_more_people = False
    for row in fetched:
        identifiers = _row_identifiers(row)
        if identifiers is None:
            continue
        application_id, candidate_id = identifiers
        if candidate_id in seen_candidates:
            continue
        seen_candidates.add(candidate_id)
        if len(selected) < safe_limit:
            selected.append((application_id, candidate_id))
        else:
            has_more_people = True

    raw_window_saturated = len(fetched) >= raw_limit
    return selected, has_more_people or raw_window_saturated


def _row_identifiers(row: object) -> tuple[int, int] | None:
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 2:
        return None
    try:
        application_id = int(row[0])
        candidate_id = int(row[1])
    except (TypeError, ValueError):
        return None
    if application_id <= 0 or candidate_id <= 0:
        return None
    return application_id, candidate_id


__all__ = [
    "MAX_PERSON_RETRIEVAL_LIMIT",
    "bounded_person_rows",
]
