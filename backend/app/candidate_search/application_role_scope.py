"""Role-local SQL and presentation helpers for application search tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.orm import Query, Session, aliased

from ..mcp.urls import application_url
from ..models.candidate_application import CandidateApplication
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .role_assessment_scores import (
    related_assessment_score_expression,
    related_taali_score_expression,
)
from .role_scope import CandidateRoleScope, RelatedRoleSearchApplication
from .role_projection import strip_owner_role_judgments


_RELATED_SCORE_FIELDS = frozenset(
    {
        "pre_screen_score_100",
        "rank_score",
        "cv_match_score",
        "role_fit_score_cache_100",
    }
)


@dataclass(frozen=True)
class AtsTransportColumns:
    """Provider fields selected from the validated optional ATS transport."""

    workable_stage: Any
    bullhorn_status: Any
    external_stage_raw: Any
    external_stage_normalized: Any


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
    if scope.role_id is None:
        return literal(None)
    if score_field == "assessment_score_cache_100":
        return related_assessment_score_expression(
            organization_id=scope.organization_id,
            role_id=int(scope.role_id),
        )
    if score_field == "taali_score_cache_100":
        return related_taali_score_expression(
            organization_id=scope.organization_id,
            role_id=int(scope.role_id),
            role_fit_expression=SisterRoleEvaluation.role_fit_score,
        )
    if score_field in _RELATED_SCORE_FIELDS:
        return SisterRoleEvaluation.role_fit_score
    if score_field == "workable_score":
        # A provider verdict on the ATS owner cannot become a related-role
        # score. External stage remains restriction context only.
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


def with_ats_transport(
    scope: CandidateRoleScope,
    query: Query,
) -> tuple[Query, AtsTransportColumns]:
    """Attach canonical provider fields without changing logical membership."""

    field_names = (
        "workable_stage",
        "bullhorn_status",
        "external_stage_raw",
        "external_stage_normalized",
    )
    if not scope.is_related:
        return query, AtsTransportColumns(
            **{name: getattr(CandidateApplication, name) for name in field_names}
        )

    ats_owner_role_id = scope.application_role_id
    if ats_owner_role_id is None:
        return query, AtsTransportColumns(
            **{name: literal(None) for name in field_names}
        )

    ats_application = aliased(
        CandidateApplication,
        name="related_role_ats_application",
    )
    query = query.outerjoin(
        ats_application,
        and_(
            ats_application.id == SisterRoleEvaluation.ats_application_id,
            ats_application.organization_id == scope.organization_id,
            ats_application.organization_id == SisterRoleEvaluation.organization_id,
            ats_application.candidate_id == SisterRoleEvaluation.candidate_id,
            ats_application.role_id == int(ats_owner_role_id),
            ats_application.deleted_at.is_(None),
        ),
    )
    legacy_source = and_(
        SisterRoleEvaluation.ats_application_id.is_(None),
        CandidateApplication.role_id == int(ats_owner_role_id),
        CandidateApplication.deleted_at.is_(None),
    )

    def _field(name: str) -> Any:
        return case(
            (ats_application.id.isnot(None), getattr(ats_application, name)),
            (legacy_source, getattr(CandidateApplication, name)),
            else_=None,
        )

    return query, AtsTransportColumns(**{name: _field(name) for name in field_names})


def filter_by_ats_stage(
    scope: CandidateRoleScope,
    query: Query,
    *,
    ats_stage: str,
) -> Query:
    """Filter on the selected role's explicit ATS transport, if any.

    A related role can own a direct, role-local source application while its
    optional external-ATS transport is a different application linked through
    ``SisterRoleEvaluation.ats_application_id``.  Filtering the source row in
    that case creates false exact-zero answers.  Keep the transport join here,
    alongside the rest of the role-scope SQL, so every caller uses the same
    membership/transport boundary.

    The source-row branch is rolling-deploy compatibility for pre-migration
    memberships only: it applies solely when no explicit ATS application is
    linked and the source row belongs to the validated ATS owner role.
    """

    query, transport = with_ats_transport(scope, query)
    return query.filter(ats_stage_match_expression(transport, ats_stage))


def ats_stage_match_expression(
    transport: AtsTransportColumns,
    ats_stage: str,
) -> Any:
    """Case-insensitive provider-neutral stage predicate for one transport."""

    like = f"%{str(ats_stage).strip()}%"
    return or_(
        transport.workable_stage.ilike(like),
        transport.bullhorn_status.ilike(like),
        transport.external_stage_raw.ilike(like),
        transport.external_stage_normalized.ilike(like),
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
                "score_mode": application.score_mode_cache,
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
    "AtsTransportColumns",
    "application_outcome_expression",
    "ats_stage_match_expression",
    "build_role_local_projection",
    "filter_by_ats_stage",
    "pipeline_stage_expression",
    "scope_with_evaluations",
    "score_expression",
    "strip_owner_role_judgments",
    "with_ats_transport",
]
