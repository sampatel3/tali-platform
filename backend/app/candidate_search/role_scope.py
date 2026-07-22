"""Role-aware candidate-search scope and presentation.

Candidate evidence may be stored on an application created for this role or
on an application linked only as an ATS transport. A related (``sister``) role
still owns its roster, score, pipeline, outcome, decisions, and history through
the explicit :class:`SisterRoleEvaluation` membership. Search must therefore
resolve both identities instead of treating the evidence/transport row as the
logical role or as membership authority.

This module is the shared boundary used by every candidate-search surface.  It
keeps tenant and lifecycle authority in PostgreSQL and exposes a read-only
application view so the existing retrieval/grounding engine can operate on a
related role without leaking the owner role's score or evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import and_, func
from sqlalchemy.orm import Query, Session

from ..mcp.urls import application_url
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SisterRoleEvaluation,
)
from ..services.auto_threshold_service import resolve_role_fit_threshold
from ..services.sister_role_projection import project_sister_application
from .role_projection import strip_owner_role_judgments


_ROLE_FIT_SCORE_FIELDS = frozenset(
    {
        "taali_score_cache_100",
        "pre_screen_score_100",
        "rank_score",
        "cv_match_score",
        "role_fit_score_cache_100",
    }
)

_OWNER_ROLE_SCORE_FIELDS = frozenset({"workable_score"})


class RelatedRoleSearchApplication:
    """Read-only role-local view over one persisted evidence row."""

    def __init__(
        self,
        source_application: CandidateApplication,
        *,
        role: Role,
        evaluation: SisterRoleEvaluation | None,
    ) -> None:
        self.source_application = source_application
        self.related_role = role
        self.evaluation = evaluation
        details = (
            dict(evaluation.details)
            if evaluation is not None and isinstance(evaluation.details, dict)
            else {}
        )
        if (
            evaluation is not None
            and evaluation.summary
            and not details.get("summary")
        ):
            details["summary"] = evaluation.summary
        self._evaluation_details = details or None

    def __getattr__(self, name: str) -> Any:
        evaluation = self.evaluation
        if name in _ROLE_FIT_SCORE_FIELDS:
            return evaluation.role_fit_score if evaluation is not None else None
        if name in _OWNER_ROLE_SCORE_FIELDS:
            return None
        if name == "role_id":
            return self.related_role.id
        if name == "role":
            return self.related_role
        if name in {"created_at", "applied_at"}:
            return evaluation.created_at if evaluation is not None else None
        if name in {"updated_at", "last_activity_at"}:
            return evaluation.updated_at if evaluation is not None else None
        if name == "pipeline_stage":
            return (
                str(evaluation.pipeline_stage or "applied")
                if evaluation is not None
                else "applied"
            )
        if name == "pipeline_stage_updated_at":
            return (
                evaluation.pipeline_stage_updated_at
                if evaluation is not None
                else None
            )
        if name == "pipeline_stage_source":
            return (
                str(evaluation.pipeline_stage_source or "system")
                if evaluation is not None
                else "system"
            )
        if name == "application_outcome":
            return (
                str(evaluation.application_outcome or "open")
                if evaluation is not None
                else "open"
            )
        if name == "application_outcome_updated_at":
            return (
                evaluation.application_outcome_updated_at
                if evaluation is not None
                else None
            )
        if name == "application_outcome_source":
            return (
                str(evaluation.application_outcome_source or "system")
                if evaluation is not None
                else "system"
            )
        if name in {"ats_context", "action_restrictions"}:
            from ..services.sister_role_projection import related_role_ats_state

            ats_state = related_role_ats_state(
                sister_role=self.related_role,
                evaluation=evaluation,
                source_application=self.source_application,
            )
            return ats_state[name]
        if name == "cv_match_details":
            return self._evaluation_details
        if name == "score_mode_cache":
            return "sister_role"
        if name in {
            "assessment_score_cache_100",
            "pre_screen_recommendation",
            "pre_screen_evidence",
            "auto_reject_state",
            "auto_reject_reason",
        }:
            return None
        return getattr(self.source_application, name)


@dataclass(frozen=True)
class CandidateRoleScope:
    """The selected product role and its optional ATS transport owner."""

    organization_id: int
    requested_role: Role | None
    application_role: Role | None

    @property
    def role_id(self) -> int | None:
        return (
            int(self.requested_role.id)
            if self.requested_role is not None
            else None
        )

    @property
    def application_role_id(self) -> int | None:
        return (
            int(self.application_role.id)
            if self.application_role is not None
            else None
        )

    @property
    def is_related(self) -> bool:
        return bool(
            self.requested_role is not None
            and (
                str(self.requested_role.role_kind or "") == ROLE_KIND_SISTER
                or self.requested_role.ats_owner_role_id is not None
            )
        )

    def scope_roster(self, query: Query) -> Query:
        """Apply the selected role's explicit candidate-membership boundary."""

        if self.role_id is None:
            return query
        if self.is_related:
            return query.join(
                SisterRoleEvaluation,
                and_(
                    SisterRoleEvaluation.organization_id == self.organization_id,
                    SisterRoleEvaluation.role_id == int(self.role_id),
                    SisterRoleEvaluation.source_application_id
                    == CandidateApplication.id,
                    SisterRoleEvaluation.deleted_at.is_(None),
                ),
            )
        return query.filter(
            CandidateApplication.role_id == int(self.role_id)
        )

    def scope_visible_roster(self, query: Query) -> Query:
        """Apply role membership and the correct evidence-row lifecycle rule.

        Ordinary-role membership is the live application row itself. Related-
        role membership is the live evaluation row, so soft deletion of its
        evidence or ATS transport cannot silently delete the candidate from
        this independent role.
        """

        scoped = self.scope_roster(query)
        if self.is_related:
            return scoped
        return scoped.filter(CandidateApplication.deleted_at.is_(None))

    def roster_size(self, db: Session) -> int:
        """Count non-deleted source applications in the selected role roster."""

        if self.is_related and self.role_id is not None:
            return int(
                db.query(func.count(SisterRoleEvaluation.id))
                .filter(
                    SisterRoleEvaluation.organization_id == self.organization_id,
                    SisterRoleEvaluation.role_id == int(self.role_id),
                    SisterRoleEvaluation.deleted_at.is_(None),
                )
                .scalar()
                or 0
            )
        query = db.query(func.count(CandidateApplication.id)).filter(
            CandidateApplication.organization_id == self.organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        if self.role_id is not None:
            query = query.filter(CandidateApplication.role_id == int(self.role_id))
        return int(query.scalar() or 0)

    def evaluation_map(
        self,
        db: Session,
        *,
        application_ids: list[int] | None = None,
    ) -> dict[int, SisterRoleEvaluation]:
        if not self.is_related or self.role_id is None:
            return {}
        query = db.query(SisterRoleEvaluation).filter(
            SisterRoleEvaluation.organization_id == self.organization_id,
            SisterRoleEvaluation.role_id == int(self.role_id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        if application_ids is not None:
            if not application_ids:
                return {}
            query = query.filter(
                SisterRoleEvaluation.source_application_id.in_(application_ids)
            )
        return {
            int(row.source_application_id): row
            for row in query.all()
        }

    def row_adapter(
        self,
        evaluations: dict[int, SisterRoleEvaluation],
    ) -> (
        Callable[
            [CandidateApplication],
            CandidateApplication | RelatedRoleSearchApplication,
        ]
        | None
    ):
        if not self.is_related or self.requested_role is None:
            return None

        def adapt(
            application: CandidateApplication,
        ) -> RelatedRoleSearchApplication:
            return RelatedRoleSearchApplication(
                application,
                role=self.requested_role,
                evaluation=evaluations.get(int(application.id)),
            )

        return adapt

    def bounded_row_adapter(self, db: Session) -> RelatedRoleRowAdapter | None:
        """Adapt only rows the ranking engine actually hydrates."""

        if not self.is_related or self.requested_role is None:
            return None
        return RelatedRoleRowAdapter(db, self)


class RelatedRoleRowAdapter:
    """Batch-load related evaluations for each bounded hydrated window."""

    def __init__(self, db: Session, scope: CandidateRoleScope) -> None:
        self.db = db
        self.scope = scope
        self.evaluations: dict[int, SisterRoleEvaluation] = {}
        self.loaded_ids: set[int] = set()

    def prepare(self, applications: list[CandidateApplication]) -> None:
        ids = {int(application.id) for application in applications} - self.loaded_ids
        if ids:
            self.evaluations.update(
                self.scope.evaluation_map(self.db, application_ids=sorted(ids))
            )
            self.loaded_ids.update(ids)

    def __call__(
        self, application: CandidateApplication
    ) -> RelatedRoleSearchApplication:
        self.prepare([application])
        assert self.scope.requested_role is not None
        return RelatedRoleSearchApplication(
            application,
            role=self.scope.requested_role,
            evaluation=self.evaluations.get(int(application.id)),
        )


@dataclass(frozen=True)
class TopCandidateRoleScope:
    """Role-specific inputs consumed by the grounded top-candidate engine."""

    base_query: Query
    score_expression: Any
    row_adapter: Callable[[CandidateApplication], Any] | None
    payload_transform: Callable[[Any, dict[str, Any]], dict[str, Any]] | None
    roster_size: int


def resolve_candidate_role_scope(
    db: Session,
    *,
    organization_id: int,
    role_id: int | None,
) -> CandidateRoleScope:
    """Validate ``role_id`` and resolve its canonical application owner."""

    if role_id is None:
        return CandidateRoleScope(
            organization_id=int(organization_id),
            requested_role=None,
            application_role=None,
        )
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if role is None:
        raise ValueError(f"role {role_id} not found")
    is_related = bool(
        str(role.role_kind or "") == ROLE_KIND_SISTER
        or role.ats_owner_role_id is not None
    )
    if not is_related:
        return CandidateRoleScope(
            organization_id=int(organization_id),
            requested_role=role,
            application_role=role,
        )
    owner = None
    if role.ats_owner_role_id is not None:
        owner = (
            db.query(Role)
            .filter(
                Role.id == int(role.ats_owner_role_id),
                Role.organization_id == int(organization_id),
                Role.deleted_at.is_(None),
            )
            .one_or_none()
        )
    return CandidateRoleScope(
        organization_id=int(organization_id),
        requested_role=role,
        # A missing, deleted, or malformed transport link never changes the
        # logical membership authority. Callers must handle no ATS owner
        # explicitly instead of pretending the related role owns ATS rows.
        application_role=owner,
    )


def build_top_candidate_role_scope(
    db: Session,
    *,
    scope: CandidateRoleScope,
    rank_by: str,
    score_field: str,
) -> TopCandidateRoleScope:
    """Build the role-local actionable query and presentation adapter."""

    base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == scope.organization_id,
    )
    roster_size = scope.roster_size(db)
    if not scope.is_related:
        base = scope.scope_roster(base).filter(
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            func.lower(func.coalesce(CandidateApplication.pipeline_stage, ""))
            != "advanced",
        )
        score_expression = getattr(CandidateApplication, score_field)
        base = base.filter(
            score_expression.isnot(None),
            func.lower(
                func.trim(
                    func.coalesce(
                        CandidateApplication.pre_screen_recommendation,
                        "",
                    )
                )
            )
            != "below threshold",
        )
        return TopCandidateRoleScope(
            base_query=base,
            score_expression=score_expression,
            row_adapter=None,
            payload_transform=None,
            roster_size=roster_size,
        )

    assert scope.requested_role is not None
    if rank_by in {"assessment", "workable"}:
        raise ValueError(
            f"{rank_by} ranking is not available for related-role searches"
        )
    cutoff = resolve_role_fit_threshold(db, role=scope.requested_role)
    if cutoff is None:
        cutoff = float(
            scope.requested_role.score_threshold
            if scope.requested_role.score_threshold is not None
            else 50
        )
    score_expression = SisterRoleEvaluation.role_fit_score
    base = (
        scope.scope_roster(base)
        .filter(
            SisterRoleEvaluation.application_outcome == "open",
            SisterRoleEvaluation.status == SISTER_EVAL_DONE,
            SisterRoleEvaluation.role_fit_score.isnot(None),
            SisterRoleEvaluation.role_fit_score >= float(cutoff),
            func.lower(
                func.trim(
                    func.coalesce(
                        SisterRoleEvaluation.pipeline_stage,
                        "applied",
                    )
                )
            )
            != "advanced",
        )
    )
    row_adapter = scope.bounded_row_adapter(db)

    def project_payload(
        application: RelatedRoleSearchApplication,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        source = application.source_application
        source_payload = dict(payload)
        source_payload.update(
            {
                "taali_score": source.taali_score_cache_100,
                "workable_disqualified": source.workable_disqualified,
            }
        )
        if scope.application_role is not None:
            source_payload.update(
                {
                    "role_id": int(scope.application_role.id),
                    "role_name": scope.application_role.name,
                }
            )
        projected = project_sister_application(
            source_payload,
            sister_role=scope.requested_role,
            owner_role=scope.application_role,
            evaluation=application.evaluation,
        )
        for timestamp_field in (
            "pipeline_stage_updated_at",
            "cv_match_scored_at",
        ):
            timestamp = projected.get(timestamp_field)
            if timestamp is not None and hasattr(timestamp, "isoformat"):
                projected[timestamp_field] = timestamp.isoformat()
        projected["frontend_url"] = application_url(
            int(source.id),
            role_id=int(scope.requested_role.id),
        )
        return strip_owner_role_judgments(projected)

    return TopCandidateRoleScope(
        base_query=base,
        score_expression=score_expression,
        row_adapter=row_adapter,
        payload_transform=project_payload,
        roster_size=roster_size,
    )


__all__ = [
    "CandidateRoleScope",
    "RelatedRoleSearchApplication",
    "TopCandidateRoleScope",
    "build_top_candidate_role_scope",
    "resolve_candidate_role_scope",
]
