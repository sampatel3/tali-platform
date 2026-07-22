"""Role-local SQL and presentation helpers for application search tools."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import and_, case, func, literal
from sqlalchemy.orm import Query, Session

from ..mcp.urls import application_url
from ..models.candidate_application import CandidateApplication
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .role_scope import CandidateRoleScope, RelatedRoleSearchApplication


_RELATED_SCORE_FIELDS = frozenset(
    {
        "taali_score_cache_100",
        "pre_screen_score_100",
        "rank_score",
        "cv_match_score",
        "role_fit_score_cache_100",
    }
)


def _evaluation_join(scope: CandidateRoleScope) -> Any:
    if not scope.is_related or scope.role_id is None:
        raise ValueError("related-role evaluation join requested for a direct role")
    return and_(
        SisterRoleEvaluation.organization_id == scope.organization_id,
        SisterRoleEvaluation.role_id == int(scope.role_id),
        SisterRoleEvaluation.source_application_id == CandidateApplication.id,
    )


def scope_with_evaluations(
    scope: CandidateRoleScope,
    query: Query,
    *,
    required: bool = False,
) -> Query:
    """Scope a query to its owner roster and attach role-local evaluation state."""

    scoped = scope.scope_roster(query)
    if not scope.is_related:
        return scoped
    join = _evaluation_join(scope)
    return (
        scoped.join(SisterRoleEvaluation, join)
        if required
        else scoped.outerjoin(SisterRoleEvaluation, join)
    )


def score_expression(scope: CandidateRoleScope, score_field: str) -> Any:
    """Return a SQL score expression owned by the selected product role."""

    source = getattr(CandidateApplication, score_field)
    if not scope.is_related:
        return source
    if score_field in _RELATED_SCORE_FIELDS:
        return SisterRoleEvaluation.role_fit_score
    if score_field == "assessment_score_cache_100":
        # An assessment on the ATS owner cannot become a sister-role score.
        return literal(None)
    # Workable score and creation time are canonical provider/application data.
    return source


def pipeline_stage_expression(scope: CandidateRoleScope) -> Any:
    """Return the effective selected-role pipeline stage in SQL."""

    if not scope.is_related:
        return CandidateApplication.pipeline_stage
    return case(
        (
            func.lower(
                func.trim(func.coalesce(CandidateApplication.pipeline_stage, ""))
            )
            == "advanced",
            "advanced",
        ),
        else_=func.coalesce(SisterRoleEvaluation.pipeline_stage, "applied"),
    )


def build_role_local_projection(
    db: Session,
    scope: CandidateRoleScope,
) -> tuple[
    Callable[[CandidateApplication], Any] | None,
    Callable[[Any, dict[str, Any]], dict[str, Any]] | None,
]:
    """Build an in-memory row adapter and a safe search-payload transform."""

    if not scope.is_related or scope.requested_role is None:
        return None, None
    row_adapter = scope.bounded_row_adapter(db)

    def project(
        application: RelatedRoleSearchApplication,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        row = dict(payload)
        row.update(
            {
                "role_id": int(scope.requested_role.id),
                "role_name": scope.requested_role.name,
                "score_mode": "sister_role",
                "frontend_url": application_url(
                    int(application.id),
                    role_id=int(scope.requested_role.id),
                ),
            }
        )
        # These values are judgments owned by the source role. The adapted row
        # has already built every public score/stage/summary from the sister
        # evaluation, so retaining any source-only field would be misleading.
        for owner_only_field in (
            "source_role_score",
            "operational_role_id",
            "operational_role_name",
            "pre_screen_recommendation",
            "pre_screen_evidence",
            "auto_reject_reason",
        ):
            row.pop(owner_only_field, None)
        return row

    return row_adapter, project


__all__ = [
    "build_role_local_projection",
    "pipeline_stage_expression",
    "scope_with_evaluations",
    "score_expression",
]
