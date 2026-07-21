"""Small, deterministic helpers for the global application-search route."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import case

from ...candidate_search.retrieval_reporting import page_retrieval_payload
from ...models.candidate_application import CandidateApplication
from ...platform.release import runtime_release_sha


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


def enforce_provider_mode_request(
    *, nl_query: str, provider_mode: str, rerank: bool, view: str
) -> None:
    if nl_query and provider_mode == "forbid" and (rerank or view == "graph"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "candidate_search_provider_path_forbidden",
                "message": (
                    "Provider-forbidden search requires rerank=false and view=list."
                ),
            },
        )


def run_search_for_route(*, provider_mode: str, **kwargs):
    from ...candidate_search.parser import ProviderCallsForbiddenError
    from ...candidate_search.runner import run_search

    try:
        return run_search(provider_mode=provider_mode, **kwargs)
    except ProviderCallsForbiddenError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "candidate_search_provider_path_forbidden",
                "message": str(exc),
            },
        ) from exc


def release_metadata(*, provider_mode: str, nl_query: str) -> dict[str, str | None]:
    if nl_query or provider_mode == "forbid":
        return {"deployment_sha": runtime_release_sha()}
    return {}


__all__ = [
    "enforce_provider_mode_request",
    "page_retrieval_payload",
    "preferred_application_order",
    "release_metadata",
    "run_search_for_route",
]
