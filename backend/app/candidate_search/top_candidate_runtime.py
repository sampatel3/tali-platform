"""Small runtime helpers shared by grounded candidate-ranking procedures."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import case
from sqlalchemy.orm import joinedload

from ..models.candidate_application import CandidateApplication


def _adapt_rows(apps, row_adapter):
    if row_adapter is None:
        return apps
    prepare = getattr(row_adapter, "prepare", None)
    if callable(prepare):
        prepare(apps)
    return [row_adapter(app) for app in apps]


def load_candidates(
    base_query,
    *,
    matcher_ids,
    score_attr,
    size: int,
    row_adapter: Callable[[Any], Any] | None = None,
):
    """Load a bounded, score-ordered window without materialising the pool."""

    if size <= 0:
        return []
    order = [score_attr.is_(None), score_attr.desc()]
    if matcher_ids:
        order = [
            case((CandidateApplication.id.in_(matcher_ids), 0), else_=1)
        ] + order
    ids = [
        row[0]
        for row in base_query.with_entities(CandidateApplication.id)
        .order_by(*order)
        .limit(int(size))
        .all()
    ]
    if not ids:
        return []
    apps = (
        base_query.filter(CandidateApplication.id.in_(ids))
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .all()
    )
    return _adapt_rows(apps, row_adapter)


def load_candidates_by_ids(
    base_query,
    application_ids: list[int],
    *,
    row_adapter: Callable[[Any], Any] | None = None,
):
    """Hydrate a relevance-ordered id list without losing its order."""

    if not application_ids:
        return []
    apps = (
        base_query.filter(CandidateApplication.id.in_(application_ids))
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .all()
    )
    apps = _adapt_rows(apps, row_adapter)
    by_id = {int(app.id): app for app in apps}
    return [by_id[app_id] for app_id in application_ids if app_id in by_id]


def pool_count(base_query) -> int:
    """Return the actionable-pool size, degrading display metadata to zero."""

    try:
        return int(base_query.count())
    except Exception:  # noqa: BLE001 - this count is best-effort metadata
        return 0


def no_actionable_candidates_payload(
    *,
    base_payload: dict[str, Any],
    authoritative_pool_size: int,
) -> dict[str, Any]:
    """Return an honest non-verdict when the ranked slice is empty."""

    return {
        **base_payload,
        "evaluated": 0,
        "deep_checked": 0,
        "evidence_succeeded": 0,
        "shown": 0,
        "returned": 0,
        "qualified": None,
        "qualified_in_checked": 0,
        "qualified_total": None,
        "eligible_after_hard_constraints": 0,
        "search_status": "no_actionable_candidates",
        "capped": False,
        "exhaustive": False,
        "is_exact_empty": False,
        "candidates": [],
        "excluded": {
            "required_total": 0,
            "not_met_total": 0,
            "missing_total": 0,
            "partial_total": 0,
            "unverified_total": int(authoritative_pool_size),
            "by_criterion": [],
        },
        "evidence_model": None,
        "warnings": base_payload["warnings"]
        + [
            {
                "code": "no_actionable_candidates",
                "message": (
                    "The selected role has no currently eligible scored "
                    "candidates to verify. No conclusion was made about "
                    "whether its broader roster meets the requested quality."
                ),
            }
        ],
    }


def structural_zero_payload(
    *,
    base_payload: dict[str, Any],
    retrieval_exact: bool,
    covers_roster: bool,
) -> dict[str, Any]:
    """Describe a structural zero without overstating a narrowed search."""

    exact = bool(retrieval_exact and covers_roster)
    code = "no_structural_matches" if exact else "structural_retrieval_incomplete"
    return {
        **base_payload,
        "evaluated": 0,
        "deep_checked": 0,
        "shown": 0,
        "returned": 0,
        "qualified": None,
        "qualified_in_checked": 0,
        "qualified_total": 0 if exact else None,
        "eligible_after_hard_constraints": 0,
        "search_status": code,
        "capped": not exact,
        "exhaustive": exact,
        "is_exact_empty": exact,
        "candidates": [],
        "excluded": {
            "required_total": 0,
            "not_met_total": 0,
            "missing_total": 0,
            "partial_total": 0,
            "unverified_total": 0,
            "by_criterion": [],
        },
        "evidence_model": None,
        "warnings": base_payload["warnings"]
        + [
            {
                "code": code,
                "message": (
                    "No candidates matched the requested skills or titles; "
                    "unrelated candidates were not substituted."
                    if exact
                    else "No candidates were retrieved for the requested skills "
                    "or titles, but the searched actionable subset did not cover "
                    "the full roster or retrieval was incomplete. This is not an "
                    "exact zero; unrelated candidates were not substituted."
                ),
            }
        ],
    }


__all__ = [
    "load_candidates",
    "load_candidates_by_ids",
    "no_actionable_candidates_payload",
    "pool_count",
    "structural_zero_payload",
]
