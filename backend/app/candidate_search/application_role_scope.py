"""Role-local SQL and presentation helpers for application search tools."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import and_, func, literal
from sqlalchemy.orm import Query, Session

from ..mcp.urls import application_url
from ..models.candidate_application import CandidateApplication
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .role_scope import CandidateRoleScope, RelatedRoleSearchApplication
from .role_projection import strip_owner_role_judgments


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
        SisterRoleEvaluation.deleted_at.is_(None),
    )


def scope_with_evaluations(
    scope: CandidateRoleScope,
    query: Query,
    *,
    required: bool = False,
) -> Query:
    """Scope a query to its owner roster and attach role-local evaluation state."""

    # Related-role membership is explicit: ``scope_roster`` has already
    # inner-joined the live SisterRoleEvaluation row. Missing evaluations are
    # not implicit owner-roster members, regardless of ``required``.
    return scope.scope_roster(query)


def score_expression(scope: CandidateRoleScope, score_field: str) -> Any:
    """Return a SQL score expression owned by the selected product role."""

    source = getattr(CandidateApplication, score_field)
    if not scope.is_related:
        return source
    if score_field in _RELATED_SCORE_FIELDS:
        return SisterRoleEvaluation.role_fit_score
    if score_field in {"assessment_score_cache_100", "workable_score"}:
        # An assessment or provider verdict on the ATS owner cannot become a
        # related-role score. External stage remains available separately as
        # transport/restriction context.
        return literal(None)
    if score_field == "created_at":
        return SisterRoleEvaluation.created_at
    return source


def pipeline_stage_expression(scope: CandidateRoleScope) -> Any:
    """Return the effective selected-role pipeline stage in SQL."""

    if not scope.is_related:
        return CandidateApplication.pipeline_stage
    return func.coalesce(SisterRoleEvaluation.pipeline_stage, "applied")


def application_outcome_expression(scope: CandidateRoleScope) -> Any:
    """Return the selected role's local application outcome in SQL."""

    if not scope.is_related:
        return CandidateApplication.application_outcome
    return func.coalesce(SisterRoleEvaluation.application_outcome, "open")


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
        return strip_owner_role_judgments(row)

    return row_adapter, project


__all__ = [
    "application_outcome_expression",
    "build_role_local_projection",
    "pipeline_stage_expression",
    "scope_with_evaluations",
    "score_expression",
    "strip_owner_role_judgments",
]
