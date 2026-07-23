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

from sqlalchemy import and_, case, false, func
from sqlalchemy.orm import Query, Session, joinedload

from ..mcp.urls import application_url
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SisterRoleEvaluation,
)
from ..services.sister_role_projection import project_sister_application
from .logical_application_scope import (
    LogicalApplicationSelection,
    LogicalMembershipKey,
    resolve_logical_application_selection,
)
from .role_assessment_scores import (
    assessment_scores_by_logical_membership,
    related_assessment_score_expression,
    related_taali_score,
    related_taali_score_expression,
)
from .role_projection import strip_owner_role_judgments


_ROLE_FIT_SCORE_FIELDS = frozenset(
    {
        "pre_screen_score_100",
        "rank_score",
        "cv_match_score",
        "role_fit_score_cache_100",
    }
)

_OWNER_ROLE_SCORE_FIELDS = frozenset({"workable_score"})

_ATS_TRANSPORT_FIELDS = frozenset(
    {
        "external_refs",
        "external_stage_raw",
        "external_stage_normalized",
        "integration_sync_state",
        "workable_candidate_id",
        "workable_stage",
        "workable_stage_local_write_at",
        "workable_sourced",
        "workable_profile_url",
        "workable_disqualified",
        "workable_disqualified_at",
        "workable_answers",
        "workable_comments",
        "workable_activities",
        "bullhorn_job_submission_id",
        "bullhorn_status",
        "bullhorn_status_local_write_at",
        "workable_created_at",
        "last_synced_at",
    }
)


class RelatedRoleSearchApplication:
    """Read-only role-local view over one persisted evidence row."""

    def __init__(
        self,
        source_application: CandidateApplication,
        *,
        role: Role,
        evaluation: SisterRoleEvaluation | None,
        assessment_score: float | None,
    ) -> None:
        self.source_application = source_application
        self.related_role = role
        self.evaluation = evaluation
        self.role_assessment_score = assessment_score
        details = (
            dict(evaluation.details)
            if evaluation is not None and isinstance(evaluation.details, dict)
            else {}
        )
        if evaluation is not None and evaluation.summary and not details.get("summary"):
            details["summary"] = evaluation.summary
        self._evaluation_details = details or None

    def __getattr__(self, name: str) -> Any:
        evaluation = self.evaluation
        if name in _ROLE_FIT_SCORE_FIELDS:
            return evaluation.role_fit_score if evaluation is not None else None
        if name == "assessment_score_cache_100":
            return self.role_assessment_score
        if name == "taali_score_cache_100":
            return related_taali_score(
                assessment_score=self.role_assessment_score,
                role_fit_score=(
                    evaluation.role_fit_score if evaluation is not None else None
                ),
            )
        if name in _OWNER_ROLE_SCORE_FIELDS:
            return None
        if name in _ATS_TRANSPORT_FIELDS:
            from ..services.sister_role_projection import (
                validated_related_role_ats_application,
            )

            transport = validated_related_role_ats_application(
                sister_role=self.related_role,
                evaluation=evaluation,
                source_application=self.source_application,
            )
            return getattr(transport, name, None)
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
                evaluation.pipeline_stage_updated_at if evaluation is not None else None
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
        if name == "manual_decision":
            return (
                evaluation.manual_decision if evaluation is not None else None
            )
        if name == "score_mode_cache":
            return (
                "assessment_plus_role_fit"
                if self.role_assessment_score is not None
                else "sister_role"
            )
        if name in {
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
        return int(self.requested_role.id) if self.requested_role is not None else None

    @property
    def application_role_id(self) -> int | None:
        return (
            int(self.application_role.id) if self.application_role is not None else None
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
        return query.filter(CandidateApplication.role_id == int(self.role_id))

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
        return {int(row.source_application_id): row for row in query.all()}

    def row_adapter(
        self,
        evaluations: dict[int, SisterRoleEvaluation],
        assessment_scores: dict[int, float] | None = None,
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
                assessment_score=(assessment_scores or {}).get(int(application.id)),
            )

        return adapt

    def assessment_score_map(
        self,
        db: Session,
        *,
        applications: list[CandidateApplication],
    ) -> dict[int, float]:
        """Return technical scores owned by this selected related role."""

        if not self.is_related or self.role_id is None:
            return {}
        logical_scores = assessment_scores_by_logical_membership(
            db,
            organization_id=self.organization_id,
            memberships=[
                (int(self.role_id), application) for application in applications
            ],
        )
        return {
            application_id: score
            for (role_id, application_id), score in logical_scores.items()
            if role_id == int(self.role_id)
        }

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
        self.assessment_scores: dict[int, float] = {}
        self.loaded_ids: set[int] = set()

    def prepare(self, applications: list[CandidateApplication]) -> None:
        ids = {int(application.id) for application in applications} - self.loaded_ids
        if ids:
            self.evaluations.update(
                self.scope.evaluation_map(self.db, application_ids=sorted(ids))
            )
            selected = [
                application
                for application in applications
                if int(application.id) in ids
            ]
            self.assessment_scores.update(
                self.scope.assessment_score_map(
                    self.db,
                    applications=selected,
                )
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
            assessment_score=self.assessment_scores.get(int(application.id)),
        )


def hydrate_logical_candidate_rows(
    db: Session,
    *,
    selection: LogicalApplicationSelection,
    keys: list[LogicalMembershipKey] | tuple[LogicalMembershipKey, ...],
) -> list[CandidateApplication | RelatedRoleSearchApplication]:
    """Hydrate logical keys without collapsing memberships sharing one row."""

    normalized = list(
        dict.fromkeys((int(role_id), int(app_id)) for role_id, app_id in keys)
    )
    if not normalized:
        return []
    application_ids = sorted({application_id for _, application_id in normalized})
    source_by_id = {
        int(application.id): application
        for application in db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .filter(
            CandidateApplication.organization_id == int(selection.organization_id),
            CandidateApplication.id.in_(application_ids),
        )
        .all()
    }
    memberships = selection.resolve_memberships(db, normalized)
    related_sources_by_role: dict[int, list[CandidateApplication]] = {}
    for key in normalized:
        membership = memberships.get(key)
        source = source_by_id.get(key[1])
        if membership is not None and membership.is_related and source is not None:
            related_sources_by_role.setdefault(key[0], []).append(source)
    assessment_score_by_key = assessment_scores_by_logical_membership(
        db,
        organization_id=int(selection.organization_id),
        memberships=[
            (role_id, source)
            for role_id, sources in related_sources_by_role.items()
            for source in sources
        ],
    )

    rows: list[CandidateApplication | RelatedRoleSearchApplication] = []
    for key in normalized:
        role_id, application_id = key
        source = source_by_id.get(application_id)
        membership = memberships.get(key)
        if source is None or membership is None:
            raise RuntimeError(
                "Logical candidate membership could not be hydrated: "
                f"role_id={role_id}, application_id={application_id}"
            )
        rows.append(
            RelatedRoleSearchApplication(
                source,
                role=membership.logical_role,
                evaluation=membership.evaluation,
                assessment_score=assessment_score_by_key.get(key),
            )
            if membership.is_related
            else source
        )
    return rows


class GlobalLogicalCandidateLoader:
    """Preserve one hydrated row per global logical role membership."""

    def __init__(
        self,
        db: Session,
        *,
        selection: LogicalApplicationSelection,
        score_expression: Any,
    ) -> None:
        self.db = db
        self.selection = selection
        self.score_expression = score_expression
        self._candidate_ids_by_application: dict[int, int] = {}

    def _candidate_ids(self, application_ids: set[int]) -> set[int]:
        missing = application_ids - set(self._candidate_ids_by_application)
        if missing:
            self._candidate_ids_by_application.update(
                {
                    int(application_id): int(candidate_id)
                    for application_id, candidate_id in self.db.query(
                        CandidateApplication.id,
                        CandidateApplication.candidate_id,
                    )
                    .filter(
                        CandidateApplication.organization_id
                        == int(self.selection.organization_id),
                        CandidateApplication.id.in_(sorted(missing)),
                    )
                    .all()
                }
            )
        return {
            self._candidate_ids_by_application[application_id]
            for application_id in application_ids
            if application_id in self._candidate_ids_by_application
        }

    def filter_matches(self, base_query: Query, matcher_ids: set[int]) -> Query:
        candidate_ids = self._candidate_ids({int(item) for item in matcher_ids})
        if not candidate_ids:
            return base_query.filter(false())
        return base_query.filter(CandidateApplication.candidate_id.in_(candidate_ids))

    def is_match(self, application: Any, matcher_ids: set[int] | None) -> bool:
        if not matcher_ids:
            return False
        candidate_ids = self._candidate_ids({int(item) for item in matcher_ids})
        return int(application.candidate_id) in candidate_ids

    def load_candidates(
        self,
        base_query: Query,
        *,
        matcher_ids: set[int] | None,
        score_attr: Any,
        size: int,
    ) -> list[CandidateApplication | RelatedRoleSearchApplication]:
        if size <= 0:
            return []
        order = [score_attr.is_(None), score_attr.desc()]
        if matcher_ids:
            candidate_ids = self._candidate_ids({int(item) for item in matcher_ids})
            order.insert(
                0,
                case(
                    (CandidateApplication.candidate_id.in_(candidate_ids), 0),
                    else_=1,
                ),
            )
        logical_role_id = self.selection.logical_role_id_expression()
        keys = [
            (int(role_id), int(application_id))
            for application_id, role_id in base_query.with_entities(
                CandidateApplication.id,
                logical_role_id,
            )
            .order_by(
                *order,
                CandidateApplication.id.desc(),
                logical_role_id.desc(),
            )
            .limit(int(size))
            .all()
        ]
        return hydrate_logical_candidate_rows(
            self.db,
            selection=self.selection,
            keys=keys,
        )

    def load_candidates_by_ids(
        self,
        base_query: Query,
        application_ids: list[int],
        *,
        score_attr: Any,
    ) -> list[CandidateApplication | RelatedRoleSearchApplication]:
        if not application_ids:
            return []
        requested_ids = {int(item) for item in application_ids}
        candidate_ids = self._candidate_ids(requested_ids)
        if not candidate_ids:
            return []
        candidate_order = {
            self._candidate_ids_by_application[application_id]: position
            for position, application_id in enumerate(application_ids)
            if application_id in self._candidate_ids_by_application
        }
        logical_role_id = self.selection.logical_role_id_expression()
        candidates = base_query.filter(
            CandidateApplication.candidate_id.in_(candidate_ids)
        ).with_entities(
            CandidateApplication.id,
            CandidateApplication.candidate_id,
            logical_role_id,
            score_attr.label("logical_score"),
        )
        rows = list(candidates.all())
        rows.sort(
            key=lambda row: (
                candidate_order.get(int(row[1]), len(candidate_order)),
                row[3] is None,
                -(float(row[3]) if row[3] is not None else 0.0),
                -int(row[2]),
                -int(row[0]),
            )
        )
        return hydrate_logical_candidate_rows(
            self.db,
            selection=self.selection,
            keys=[(int(row[2]), int(row[0])) for row in rows],
        )


@dataclass(frozen=True)
class TopCandidateRoleScope:
    """Role-specific inputs consumed by the grounded top-candidate engine."""

    base_query: Query
    score_expression: Any
    row_adapter: Callable[[CandidateApplication], Any] | None
    payload_transform: Callable[[Any, dict[str, Any]], dict[str, Any]] | None
    roster_size: int
    candidate_loader: GlobalLogicalCandidateLoader | None = None


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


def _project_related_top_candidate_payload(
    application: RelatedRoleSearchApplication,
    payload: dict[str, Any],
    *,
    owner_role: Role | None,
) -> dict[str, Any]:
    source = application.source_application
    source_payload = dict(payload)
    source_payload.update(
        {
            "taali_score": source.taali_score_cache_100,
            "workable_disqualified": source.workable_disqualified,
        }
    )
    if owner_role is not None:
        source_payload.update(
            {
                "role_id": int(owner_role.id),
                "role_name": owner_role.name,
            }
        )
    projected = project_sister_application(
        source_payload,
        sister_role=application.related_role,
        owner_role=owner_role,
        evaluation=application.evaluation,
    )
    assessment_score = application.role_assessment_score
    taali_score = application.taali_score_cache_100
    projected.update(
        {
            "assessment_score": assessment_score,
            "taali_score": taali_score,
            "score_mode": application.score_mode_cache,
        }
    )
    if isinstance(projected.get("score_summary"), dict):
        projected["score_summary"].update(
            {
                "assessment_score": assessment_score,
                "taali_score": taali_score,
                "mode": application.score_mode_cache,
                "score_mode": application.score_mode_cache,
            }
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
        role_id=int(application.related_role.id),
    )
    return strip_owner_role_judgments(projected)


def build_top_candidate_role_scope(
    db: Session,
    *,
    scope: CandidateRoleScope,
    rank_by: str,
    score_field: str,
) -> TopCandidateRoleScope:
    """Build the complete active logical pool and its presentation adapter.

    Search membership comes only from the selected role and its active
    pipeline/outcome boundary. Scores and prior screening verdicts rank the
    grounded matches; they never decide whether a candidate can be searched.
    """

    base = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == scope.organization_id,
    )
    if scope.role_id is None:
        selection = resolve_logical_application_selection(
            db,
            organization_id=scope.organization_id,
            role_ids=(),
        )
        if not selection.valid_role_ids:
            base = base.filter(false())
        else:
            base = selection.apply_membership(base).filter(
                selection.application_outcome_expression() == "open",
                func.lower(
                    func.trim(
                        func.coalesce(
                            selection.pipeline_stage_expression(),
                            "applied",
                        )
                    )
                )
                != "advanced",
            )
        score_expression = selection.score_expression(score_field)
        candidate_loader = GlobalLogicalCandidateLoader(
            db,
            selection=selection,
            score_expression=score_expression,
        )

        def project_global_payload(
            application: CandidateApplication | RelatedRoleSearchApplication,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            logical_role_id = int(application.role_id)
            if isinstance(application, RelatedRoleSearchApplication):
                related_role = application.related_role
                owner_role = (
                    selection.owner_roles_by_id.get(int(related_role.ats_owner_role_id))
                    if related_role.ats_owner_role_id is not None
                    else None
                )
                projected = _project_related_top_candidate_payload(
                    application,
                    payload,
                    owner_role=owner_role,
                )
            else:
                projected = dict(payload)
            projected["logical_membership_id"] = (
                f"{logical_role_id}:{int(application.id)}"
            )
            return projected

        return TopCandidateRoleScope(
            base_query=base,
            score_expression=score_expression,
            row_adapter=None,
            payload_transform=project_global_payload,
            roster_size=int(base.order_by(None).count()),
            candidate_loader=candidate_loader,
        )

    if not scope.is_related:
        base = scope.scope_roster(base).filter(
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            func.lower(func.coalesce(CandidateApplication.pipeline_stage, ""))
            != "advanced",
        )
        score_expression = getattr(CandidateApplication, score_field)
        return TopCandidateRoleScope(
            base_query=base,
            score_expression=score_expression,
            row_adapter=None,
            payload_transform=None,
            roster_size=int(base.order_by(None).count()),
        )

    assert scope.requested_role is not None
    if rank_by == "workable":
        raise ValueError(
            f"{rank_by} ranking is not available for related-role searches"
        )
    if score_field == "assessment_score_cache_100":
        score_expression = related_assessment_score_expression(
            organization_id=scope.organization_id,
            role_id=int(scope.requested_role.id),
        )
    elif score_field == "taali_score_cache_100":
        score_expression = related_taali_score_expression(
            organization_id=scope.organization_id,
            role_id=int(scope.requested_role.id),
            role_fit_expression=SisterRoleEvaluation.role_fit_score,
        )
    else:
        score_expression = SisterRoleEvaluation.role_fit_score
    base = scope.scope_roster(base).filter(
        SisterRoleEvaluation.application_outcome == "open",
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
    row_adapter = scope.bounded_row_adapter(db)

    def project_payload(
        application: RelatedRoleSearchApplication,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return _project_related_top_candidate_payload(
            application,
            payload,
            owner_role=scope.application_role,
        )

    return TopCandidateRoleScope(
        base_query=base,
        score_expression=score_expression,
        row_adapter=row_adapter,
        payload_transform=project_payload,
        roster_size=int(base.order_by(None).count()),
    )


__all__ = [
    "CandidateRoleScope",
    "RelatedRoleSearchApplication",
    "TopCandidateRoleScope",
    "build_top_candidate_role_scope",
    "resolve_candidate_role_scope",
]
