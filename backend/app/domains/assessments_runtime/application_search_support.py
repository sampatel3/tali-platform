"""Small, deterministic helpers for the global application-search route."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case

from ...candidate_search.retrieval_reporting import page_retrieval_payload
from ...models.candidate_application import CandidateApplication


def preferred_application_order() -> tuple[Any, ...]:
    """Prefer a person's active scoped application, then the newest one."""

    return (
        case(
            (CandidateApplication.application_outcome == "open", 0),
            else_=1,
        ).asc(),
        CandidateApplication.updated_at.desc().nullslast(),
        CandidateApplication.created_at.desc().nullslast(),
        CandidateApplication.id.desc(),
    )


__all__ = ["page_retrieval_payload", "preferred_application_order"]
