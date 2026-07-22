"""Small, deterministic helpers for the global application-search route."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, func, or_

from ...candidate_search.logical_application_scope import LogicalApplicationSelection
from ...candidate_search.retrieval_reporting import page_retrieval_payload
from ...models.candidate_application import CandidateApplication
from ...models.sister_role_evaluation import SisterRoleEvaluation
from ...platform.release import runtime_release_sha
from ...services.taali_scoring import normalize_score_100


PIPELINE_STAGE_VALUES = {
    "sourced",
    "applied",
    "invited",
    "in_assessment",
    "review",
}
APPLICATION_OUTCOME_VALUES = {"open", "rejected", "withdrawn", "hired"}


def normalize_taali_score_for_filter(
    value: float | int | None,
) -> float | None:
    """Normalize stored 0-100 score values without inflating weak scores."""

    return normalize_score_100(value)


def parse_csv_tokens(raw_value: str | None) -> list[str]:
    if raw_value is None:
        return []
    return [
        token.strip()
        for token in str(raw_value).split(",")
        if token and token.strip()
    ]


def parse_int_csv_filter(raw_value: str | None, *, field_name: str) -> list[int]:
    values: list[int] = []
    for token in parse_csv_tokens(raw_value):
        try:
            parsed = int(token)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid {field_name} value '{token}'. "
                    "Expected comma-separated integers."
                ),
            ) from None
        if parsed <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid {field_name} value '{token}'. "
                    "Expected positive integers."
                ),
            )
        values.append(parsed)
    return values


def parse_choice_csv_filter(
    raw_value: str | None,
    *,
    allowed: set[str],
    field_name: str,
) -> list[str]:
    tokens = [token.lower() for token in parse_csv_tokens(raw_value)]
    if not tokens or "all" in tokens:
        return []
    invalid = [token for token in tokens if token not in allowed]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name} value(s): {', '.join(sorted(set(invalid)))}",
        )
    return list(dict.fromkeys(tokens))


def empty_stage_counts() -> dict[str, int]:
    return {
        "all": 0,
        "applied": 0,
        "invited": 0,
        "in_assessment": 0,
        "review": 0,
    }


def build_stage_counts(
    stage_rows: list[tuple[str | None, int]],
) -> dict[str, int]:
    counts = empty_stage_counts()
    for stage, total in stage_rows:
        key = str(stage or "").strip().lower()
        if key in counts:
            counts[key] = int(total or 0)
    counts["all"] = sum(
        counts[key] for key in ("applied", "invited", "in_assessment", "review")
    )
    return counts


def effective_pipeline_stage_sql(*, is_sister: bool):
    if not is_sister:
        return CandidateApplication.pipeline_stage
    return func.coalesce(SisterRoleEvaluation.pipeline_stage, "applied")


def effective_application_outcome_sql(*, is_sister: bool):
    if not is_sister:
        return CandidateApplication.application_outcome
    return func.coalesce(SisterRoleEvaluation.application_outcome, "open")


def application_order_columns(
    sort_by: str,
    sort_order: str,
    *,
    logical_selection: LogicalApplicationSelection | None = None,
):
    reverse = sort_order != "asc"

    def score(field: str):
        if logical_selection is not None and logical_selection.active:
            return logical_selection.score_expression(field)
        return getattr(CandidateApplication, field)

    if sort_by == "pre_screen_score":
        primary = func.coalesce(
            score("pre_screen_score_100"), -1.0 if reverse else 101.0
        )
    elif sort_by == "taali_score":
        primary = func.coalesce(
            score("taali_score_cache_100"),
            score("pre_screen_score_100"),
            -1.0 if reverse else 101.0,
        )
    elif sort_by == "cv_match_score":
        primary = func.coalesce(
            score("cv_match_score"), -1.0 if reverse else 101.0
        )
    elif sort_by == "cv_match_scored_at":
        unscored_anchor = (
            datetime.min.replace(tzinfo=timezone.utc)
            if reverse
            else datetime.max.replace(tzinfo=timezone.utc)
        )
        primary = func.coalesce(
            (
                logical_selection.cv_match_scored_at_expression()
                if logical_selection is not None and logical_selection.active
                else CandidateApplication.cv_match_scored_at
            ),
            unscored_anchor,
        )
    elif sort_by == "created_at":
        primary = (
            logical_selection.created_at_expression()
            if logical_selection is not None and logical_selection.active
            else CandidateApplication.created_at
        )
    else:
        primary = func.coalesce(
            (
                logical_selection.pipeline_stage_updated_at_expression()
                if logical_selection is not None and logical_selection.active
                else CandidateApplication.pipeline_stage_updated_at
            ),
            CandidateApplication.updated_at,
            CandidateApplication.created_at,
        )
    direction = primary.desc if reverse else primary.asc
    created_expression = (
        logical_selection.created_at_expression()
        if logical_selection is not None and logical_selection.active
        else CandidateApplication.created_at
    )
    created_direction = created_expression.desc if reverse else created_expression.asc
    id_direction = (
        CandidateApplication.id.desc if reverse else CandidateApplication.id.asc
    )
    return [direction(), created_direction(), id_direction()]


def apply_application_source_filter(query, source: str | None):
    normalized = str(source or "").strip().lower()
    if normalized == "workable":
        return query.filter(
            or_(
                CandidateApplication.source == "workable",
                CandidateApplication.workable_sourced.is_(True),
            )
        )
    if normalized == "manual":
        return query.filter(
            CandidateApplication.source != "workable",
            or_(
                CandidateApplication.workable_sourced.is_(False),
                CandidateApplication.workable_sourced.is_(None),
            ),
        )
    return query


def preferred_application_order(
    *,
    application_outcome: Any = CandidateApplication.application_outcome,
) -> tuple[Any, ...]:
    """Prefer a person's active scoped application, then the newest one."""

    return (
        case(
            (application_outcome == "open", 0),
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
    "APPLICATION_OUTCOME_VALUES",
    "PIPELINE_STAGE_VALUES",
    "application_order_columns",
    "apply_application_source_filter",
    "build_stage_counts",
    "effective_application_outcome_sql",
    "effective_pipeline_stage_sql",
    "empty_stage_counts",
    "enforce_provider_mode_request",
    "normalize_taali_score_for_filter",
    "page_retrieval_payload",
    "parse_choice_csv_filter",
    "parse_int_csv_filter",
    "preferred_application_order",
    "release_metadata",
    "run_search_for_route",
]
