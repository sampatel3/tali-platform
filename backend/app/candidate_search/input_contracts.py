"""Final paid-boundary contracts for candidate-search text."""

from __future__ import annotations

from typing import Any


CANDIDATE_SEARCH_QUERY_MAX_LENGTH = 2_000


def bounded_candidate_search_query(
    value: Any,
    *,
    allow_empty: bool = False,
) -> str:
    """Normalize valid text and reject unbounded input without truncation.

    Parser internals may preserve their historical empty-query short circuit;
    HTTP/provider entry points keep the stricter non-empty default.
    """

    if not isinstance(value, str):
        raise ValueError("candidate search query must be a string")
    if len(value) > CANDIDATE_SEARCH_QUERY_MAX_LENGTH:
        raise ValueError(
            "candidate search query must be at most "
            f"{CANDIDATE_SEARCH_QUERY_MAX_LENGTH} characters"
        )
    query = value.strip()
    if not query and not allow_empty:
        raise ValueError("candidate search query must be non-empty")
    return query


__all__ = [
    "CANDIDATE_SEARCH_QUERY_MAX_LENGTH",
    "bounded_candidate_search_query",
]
