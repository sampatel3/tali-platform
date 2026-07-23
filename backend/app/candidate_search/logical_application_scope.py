"""Canonical logical-role membership for application queries.

Physical ``CandidateApplication.role_id`` is sufficient for ordinary roles.
Related roles additionally own explicit ``SisterRoleEvaluation`` membership,
whose source application may be an ATS-owner row, a direct related-role row,
or soft-deleted evidence. This module gives list/search/analytics callers one
authorization boundary and one set of role-local SQL expressions without
teaching each surface those storage details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import and_, case, false, func, literal, or_, select
from sqlalchemy.orm import Query, Session

from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .assessment_score_truth import (
    assessment_snapshot_role_fit_value_expression,
    blended_taali_score_expression,
)
from .role_assessment_scores import (
    assessment_score_value_expression,
    normalized_score_expression,
)
from .population import apply_searchable_candidate_scope


LogicalMembershipKey = tuple[int, int]


@dataclass(frozen=True)
class LogicalApplicationMembership:
    """One physical application viewed through one selected logical role."""

    application_id: int
    logical_role: Role
    evaluation: SisterRoleEvaluation | None
    ats_owner_role: Role | None

    @property
    def key(self) -> LogicalMembershipKey:
        return (int(self.logical_role.id), int(self.application_id))

    @property
    def public_id(self) -> str:
        """Stable composite identity for clients rendering mixed role pools."""

        return f"{int(self.logical_role.id)}:{int(self.application_id)}"

    @property
    def is_related(self) -> bool:
        return bool(
            str(self.logical_role.role_kind or "") == ROLE_KIND_SISTER
            or self.logical_role.ats_owner_role_id is not None
        )


@dataclass(frozen=True)
class LogicalApplicationSelection:
    """A union containing exactly one row per logical role membership.

    A source application selected through both an ordinary owner role and an
    independent related role intentionally appears twice: those memberships
    can have divergent scores, stages and outcomes. SQL ``UNION`` removes only
    duplicate representations of the *same* ``(logical_role_id,
    application_id)`` pair, such as a direct related application that also has
    its required live evaluation row.
    """

    organization_id: int
    requested_role_ids: tuple[int, ...]
    roles_by_id: Mapping[int, Role]
    owner_roles_by_id: Mapping[int, Role]
    related_role_ids: tuple[int, ...]
    membership_rows: Any
    assessment_rows: Any

    @property
    def active(self) -> bool:
        return bool(self.requested_role_ids)

    @property
    def valid_role_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.roles_by_id))

    def apply_membership(self, query: Query) -> Query:
        """Join membership and one role-local technical-assessment snapshot."""

        joined = self.apply_roster_membership(query)
        if not self.active:
            return joined
        return joined.outerjoin(
            self.assessment_rows,
            and_(
                self.assessment_rows.c.role_id == self.logical_role_id_expression(),
                self.assessment_rows.c.candidate_id
                == CandidateApplication.candidate_id,
            ),
        )

    def apply_roster_membership(self, query: Query) -> Query:
        """Join the logical roster and enforce the shared person lifecycle."""

        if not self.active:
            joined = query.filter(CandidateApplication.deleted_at.is_(None))
        else:
            joined = query.join(
                self.membership_rows,
                self.membership_rows.c.application_id == CandidateApplication.id,
            )
        return apply_searchable_candidate_scope(
            joined,
            organization_id=self.organization_id,
        )

    def logical_role_id_expression(self) -> Any:
        return self.membership_rows.c.logical_role_id

    def application_id_expression(self) -> Any:
        return self.membership_rows.c.application_id

    def _evaluation_filters(self) -> tuple[Any, ...]:
        return (
            SisterRoleEvaluation.organization_id == int(self.organization_id),
            SisterRoleEvaluation.role_id == self.logical_role_id_expression(),
            SisterRoleEvaluation.source_application_id == CandidateApplication.id,
            SisterRoleEvaluation.deleted_at.is_(None),
        )

    def _evaluation_exists(self) -> Any:
        if not self.related_role_ids:
            return false()
        return (
            select(SisterRoleEvaluation.id)
            .where(*self._evaluation_filters())
            .correlate(CandidateApplication, self.membership_rows)
            .exists()
        )

    def _evaluation_value(self, column: Any) -> Any:
        if not self.related_role_ids:
            return literal(None)
        return (
            select(column)
            .where(*self._evaluation_filters())
            .limit(1)
            .correlate(CandidateApplication, self.membership_rows)
            .scalar_subquery()
        )

    def _uses_related_evaluation(self) -> Any:
        if not self.related_role_ids:
            return false()
        return and_(
            self.logical_role_id_expression().in_(self.related_role_ids),
            self._evaluation_exists(),
        )

    def pipeline_stage_expression(self) -> Any:
        return case(
            (
                self._uses_related_evaluation(),
                func.coalesce(
                    self._evaluation_value(SisterRoleEvaluation.pipeline_stage),
                    "applied",
                ),
            ),
            else_=CandidateApplication.pipeline_stage,
        )

    def pipeline_stage_updated_at_expression(self) -> Any:
        return case(
            (
                self._uses_related_evaluation(),
                self._evaluation_value(SisterRoleEvaluation.pipeline_stage_updated_at),
            ),
            else_=CandidateApplication.pipeline_stage_updated_at,
        )

    def application_outcome_expression(self) -> Any:
        return case(
            (
                self._uses_related_evaluation(),
                func.coalesce(
                    self._evaluation_value(SisterRoleEvaluation.application_outcome),
                    "open",
                ),
            ),
            else_=CandidateApplication.application_outcome,
        )

    def created_at_expression(self) -> Any:
        """Timestamp when this logical role membership began."""

        return case(
            (
                self._uses_related_evaluation(),
                self._evaluation_value(SisterRoleEvaluation.created_at),
            ),
            else_=CandidateApplication.created_at,
        )

    def application_outcome_updated_at_expression(self) -> Any:
        """Timestamp of the selected role's current outcome transition."""

        return case(
            (
                self._uses_related_evaluation(),
                self._evaluation_value(
                    SisterRoleEvaluation.application_outcome_updated_at
                ),
            ),
            else_=CandidateApplication.application_outcome_updated_at,
        )

    def score_expression(self, score_field: str) -> Any:
        """Return a role-owned score expression for list filters/sorts."""

        source = getattr(CandidateApplication, score_field)
        if not self.related_role_ids:
            return source
        role_fit_value = self._evaluation_value(SisterRoleEvaluation.role_fit_score)
        if score_field == "assessment_score_cache_100":
            related_value = self.assessment_rows.c.assessment_score
        elif score_field == "taali_score_cache_100":
            related_value = case(
                (
                    self.assessment_rows.c.assessment_id.isnot(None),
                    self.assessment_rows.c.taali_score,
                ),
                else_=normalized_score_expression(role_fit_value),
            )
        elif score_field == "workable_score":
            related_value = literal(None)
        elif score_field in {
            "pre_screen_score_100",
            "rank_score",
            "cv_match_score",
            "role_fit_score_cache_100",
        }:
            related_value = role_fit_value
        else:
            related_value = source
        return case(
            (self._uses_related_evaluation(), related_value),
            else_=source,
        )

    def taali_sort_expression(self) -> Any:
        """Keep ordinary cache fallback without reviving unavailable grading."""

        taali_score = self.score_expression("taali_score_cache_100")
        ordinary_score = func.coalesce(
            taali_score,
            CandidateApplication.pre_screen_score_100,
        )
        if not self.related_role_ids:
            return ordinary_score
        return case(
            (
                self.logical_role_id_expression().in_(self.related_role_ids),
                taali_score,
            ),
            else_=ordinary_score,
        )

    def cv_match_scored_at_expression(self) -> Any:
        return case(
            (
                self._uses_related_evaluation(),
                self._evaluation_value(SisterRoleEvaluation.scored_at),
            ),
            else_=CandidateApplication.cv_match_scored_at,
        )

    def related_evaluation_status_expression(self) -> Any:
        """Scoring lifecycle for related memberships; NULL for ordinary roles."""

        return case(
            (
                self._uses_related_evaluation(),
                self._evaluation_value(SisterRoleEvaluation.status),
            ),
            else_=literal(None),
        )

    def resolve_memberships(
        self,
        db: Session,
        keys: Iterable[LogicalMembershipKey],
    ) -> dict[LogicalMembershipKey, LogicalApplicationMembership]:
        """Hydrate exact logical membership keys for payload projection."""

        normalized = tuple(
            dict.fromkeys(
                (int(role_id), int(application_id)) for role_id, application_id in keys
            )
        )
        if not normalized or not self.active:
            return {}
        authorized_query = db.query(
            self.membership_rows.c.application_id,
            self.membership_rows.c.logical_role_id,
        ).join(
            CandidateApplication,
            and_(
                CandidateApplication.id == self.membership_rows.c.application_id,
                CandidateApplication.organization_id == int(self.organization_id),
            ),
        )
        authorized_query = apply_searchable_candidate_scope(
            authorized_query,
            organization_id=self.organization_id,
        )
        authorized = {
            (int(role_id), int(application_id))
            for application_id, role_id in (
                authorized_query.filter(
                    or_(
                        *(
                            and_(
                                self.membership_rows.c.logical_role_id == role_id,
                                self.membership_rows.c.application_id == application_id,
                            )
                            for role_id, application_id in normalized
                        )
                    )
                ).all()
            )
        }
        normalized = tuple(key for key in normalized if key in authorized)
        if not normalized:
            return {}
        application_ids = sorted({application_id for _, application_id in normalized})
        role_ids = sorted({role_id for role_id, _ in normalized})
        evaluations = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id == int(self.organization_id),
                SisterRoleEvaluation.role_id.in_(role_ids),
                SisterRoleEvaluation.source_application_id.in_(application_ids),
                SisterRoleEvaluation.deleted_at.is_(None),
            )
            .all()
        )
        evaluation_by_key = {
            (int(row.role_id), int(row.source_application_id)): row
            for row in evaluations
        }
        resolved: dict[LogicalMembershipKey, LogicalApplicationMembership] = {}
        for role_id, application_id in normalized:
            logical_role = self.roles_by_id.get(role_id)
            if logical_role is None:
                continue
            owner = (
                self.owner_roles_by_id.get(int(logical_role.ats_owner_role_id))
                if logical_role.ats_owner_role_id is not None
                else None
            )
            resolved[(role_id, application_id)] = LogicalApplicationMembership(
                application_id=application_id,
                logical_role=logical_role,
                evaluation=evaluation_by_key.get((role_id, application_id)),
                ats_owner_role=owner,
            )
        return resolved


def _membership_subquery(
    *,
    organization_id: int,
    valid_role_ids: tuple[int, ...],
    related_role_ids: tuple[int, ...],
) -> Any:
    direct = select(
        CandidateApplication.id.label("application_id"),
        CandidateApplication.role_id.label("logical_role_id"),
    ).where(
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.role_id.in_(valid_role_ids or (-1,)),
        # Related-role membership is explicit: the evaluation row is the
        # membership and owns local state.  A stray physical application on a
        # related role must not silently recreate the legacy implicit-pool
        # behaviour or fall back to CandidateApplication state.
        CandidateApplication.role_id.notin_(related_role_ids or (-1,)),
        CandidateApplication.deleted_at.is_(None),
    )
    related = select(
        SisterRoleEvaluation.source_application_id.label("application_id"),
        SisterRoleEvaluation.role_id.label("logical_role_id"),
    ).where(
        SisterRoleEvaluation.organization_id == int(organization_id),
        SisterRoleEvaluation.role_id.in_(related_role_ids or (-1,)),
        SisterRoleEvaluation.deleted_at.is_(None),
    )
    # UNION preserves owner + related memberships as separate pairs. Related
    # roles enter only through their explicit live membership row, including
    # candidates whose evidence application is physically stored on that role.
    return direct.union(related).subquery("logical_application_memberships")


def _assessment_subquery(*, organization_id: int) -> Any:
    """One latest completed score snapshot per logical role/candidate."""

    # Materialize the expensive legacy inputs before composing TAALI. Reusing
    # the raw SQLAlchemy expression tree directly makes every weighted-score
    # reference expand the full JSON/normalization CASE tree again. The
    # resulting global list query exceeded SQLite's parser stack on Python
    # 3.10 even though PostgreSQL accepted it. These stages preserve the same
    # row-local truth while keeping each canonical expression present once.
    ranked = (
        select(
            Assessment.id.label("assessment_id"),
            Assessment.role_id.label("role_id"),
            Assessment.candidate_id.label("candidate_id"),
            Assessment.scoring_partial.label("scoring_partial"),
            Assessment.scoring_failed.label("scoring_failed"),
            assessment_score_value_expression().label("assessment_score"),
            normalized_score_expression(Assessment.taali_score).label(
                "persisted_taali_score"
            ),
            assessment_snapshot_role_fit_value_expression().label(
                "snapshot_role_fit_score"
            ),
            func.row_number()
            .over(
                partition_by=(Assessment.role_id, Assessment.candidate_id),
                order_by=(
                    Assessment.completed_at.desc().nullslast(),
                    Assessment.created_at.desc().nullslast(),
                    Assessment.id.desc(),
                ),
            )
            .label("assessment_rank"),
        )
        .where(
            Assessment.organization_id == int(organization_id),
            Assessment.role_id.isnot(None),
            Assessment.is_voided.is_(False),
            Assessment.status.in_(
                (
                    AssessmentStatus.COMPLETED,
                    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
                )
            ),
        )
        .subquery("ranked_logical_role_assessment_inputs")
    )
    latest = (
        select(
            ranked.c.assessment_id,
            ranked.c.role_id,
            ranked.c.candidate_id,
            ranked.c.scoring_partial,
            ranked.c.scoring_failed,
            ranked.c.assessment_score,
            ranked.c.persisted_taali_score,
            ranked.c.snapshot_role_fit_score,
        )
        .where(ranked.c.assessment_rank == 1)
        .subquery("latest_logical_role_assessment_inputs")
    )
    legacy_taali_score = blended_taali_score_expression(
        assessment_expression=latest.c.assessment_score,
        role_fit_expression=latest.c.snapshot_role_fit_score,
    )
    scored = (
        select(
            latest.c.assessment_id,
            latest.c.role_id,
            latest.c.candidate_id,
            latest.c.assessment_score,
            case(
                (
                    latest.c.scoring_partial.is_(True)
                    | latest.c.scoring_failed.is_(True),
                    literal(None),
                ),
                else_=func.coalesce(
                    latest.c.persisted_taali_score,
                    legacy_taali_score,
                ),
            ).label("taali_score"),
        )
        .subquery("logical_role_assessment_scores")
    )
    return (
        select(
            scored.c.assessment_id,
            scored.c.role_id,
            scored.c.candidate_id,
            scored.c.assessment_score,
            scored.c.taali_score,
        )
        .subquery("latest_logical_role_assessment_scores")
    )


def resolve_logical_application_selection(
    db: Session,
    *,
    organization_id: int,
    role_ids: Iterable[int],
) -> LogicalApplicationSelection:
    """Resolve selected active roles without crossing the organization boundary."""

    explicitly_requested = tuple(sorted({int(role_id) for role_id in role_ids}))
    role_query = db.query(Role).filter(
        Role.organization_id == int(organization_id),
        Role.deleted_at.is_(None),
    )
    if explicitly_requested:
        role_query = role_query.filter(Role.id.in_(explicitly_requested))
    roles = role_query.all()
    roles_by_id = {int(role.id): role for role in roles}
    requested = explicitly_requested or tuple(sorted(roles_by_id))
    related_ids = tuple(
        sorted(
            int(role.id)
            for role in roles
            if str(role.role_kind or "") == ROLE_KIND_SISTER
            or role.ats_owner_role_id is not None
        )
    )
    owner_ids = {
        int(role.ats_owner_role_id)
        for role in roles
        if role.ats_owner_role_id is not None
    }
    owners = (
        db.query(Role)
        .filter(
            Role.organization_id == int(organization_id),
            Role.id.in_(owner_ids or (-1,)),
        )
        .all()
    )
    valid_ids = tuple(sorted(roles_by_id))
    return LogicalApplicationSelection(
        organization_id=int(organization_id),
        requested_role_ids=requested,
        roles_by_id=roles_by_id,
        owner_roles_by_id={int(role.id): role for role in owners},
        related_role_ids=related_ids,
        membership_rows=_membership_subquery(
            organization_id=int(organization_id),
            valid_role_ids=valid_ids,
            related_role_ids=related_ids,
        ),
        assessment_rows=_assessment_subquery(
            organization_id=int(organization_id),
        ),
    )


__all__ = [
    "LogicalApplicationMembership",
    "LogicalApplicationSelection",
    "LogicalMembershipKey",
    "resolve_logical_application_selection",
]
