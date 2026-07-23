"""Canonical authorization for role-bound candidate reads and mutations.

An autonomous agent acts for one logical role.  For an ordinary role the live
``CandidateApplication`` row is that role's membership.  For a related role the
live ``SisterRoleEvaluation`` row is the membership and the linked application
is evidence/optional ATS transport only.  Callers must not infer authority from
the physical application's ``role_id``.

This module centralizes that distinction and validates a whole requested batch
before a caller performs any mutation.  A mixed valid/invalid batch therefore
fails closed instead of spending against or changing the valid subset.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from sqlalchemy.orm import Session, joinedload

from ..candidate_search.role_assessment_scores import (
    RoleAssessmentTruth,
    assessment_truth_by_logical_membership,
)
from ..candidate_search.role_scope import (
    RelatedRoleSearchApplication,
    resolve_candidate_role_scope,
)
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation


class LogicalRoleApplicationAuthorizationError(ValueError):
    """The requested subject is not in the active logical role's live pool."""

    def __init__(
        self,
        message: str,
        *,
        role_id: int,
        application_ids: Iterable[int] = (),
        candidate_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.role_id = int(role_id)
        self.application_ids = tuple(int(value) for value in application_ids)
        self.candidate_id = int(candidate_id) if candidate_id is not None else None


@dataclass(frozen=True)
class LogicalRoleApplicationContext:
    """One authorized logical-role membership and its evidence/transport rows."""

    role: Role
    source_application: CandidateApplication
    related_evaluation: SisterRoleEvaluation | None = None
    ats_application: CandidateApplication | None = None
    assessment_score: float | None = None
    assessment_truth: RoleAssessmentTruth | None = None

    @property
    def is_related(self) -> bool:
        return self.related_evaluation is not None

    @property
    def application_id(self) -> int:
        return int(self.source_application.id)

    @property
    def candidate_id(self) -> int:
        return int(self.source_application.candidate_id)

    @property
    def candidate(self) -> Candidate:
        candidate = self.source_application.candidate
        if candidate is None:  # pragma: no cover - guarded by the scoped query
            raise LogicalRoleApplicationAuthorizationError(
                "Candidate is unavailable for this role.",
                role_id=int(self.role.id),
                application_ids=(int(self.source_application.id),),
            )
        return candidate

    @property
    def presented_application(
        self,
    ) -> CandidateApplication | RelatedRoleSearchApplication:
        if self.related_evaluation is None:
            return self.source_application
        return RelatedRoleSearchApplication(
            self.source_application,
            role=self.role,
            evaluation=self.related_evaluation,
            assessment_score=self.assessment_score,
            assessment_truth=self.assessment_truth,
        )


def _normalized_application_ids(values: Iterable[int]) -> tuple[int, ...]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        application_id = int(value)
        if application_id <= 0:
            raise ValueError("application ids must be positive integers")
        if application_id not in seen:
            normalized.append(application_id)
            seen.add(application_id)
    return tuple(normalized)


def _scoped_contexts(
    db: Session,
    *,
    role: Role,
    application_ids: tuple[int, ...] | None = None,
    candidate_id: int | None = None,
) -> list[LogicalRoleApplicationContext]:
    organization_id = int(role.organization_id)
    role_id = int(role.id)
    scope = resolve_candidate_role_scope(
        db,
        organization_id=organization_id,
        role_id=role_id,
    )
    if scope.requested_role is None:
        raise LogicalRoleApplicationAuthorizationError(
            "The acting role is unavailable.",
            role_id=role_id,
            application_ids=application_ids or (),
            candidate_id=candidate_id,
        )

    query = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            Candidate.organization_id == organization_id,
            Candidate.deleted_at.is_(None),
        )
    )
    query = scope.scope_visible_roster(query)
    if application_ids is not None:
        query = query.filter(CandidateApplication.id.in_(application_ids))
    if candidate_id is not None:
        query = query.filter(CandidateApplication.candidate_id == int(candidate_id))
    applications = query.order_by(CandidateApplication.id.asc()).all()

    evaluations = scope.evaluation_map(
        db,
        application_ids=[int(application.id) for application in applications],
    )
    assessment_truth = (
        assessment_truth_by_logical_membership(
            db,
            organization_id=organization_id,
            memberships=[(role_id, application) for application in applications],
        )
        if scope.is_related
        else {}
    )
    ats_ids = sorted(
        {
            int(evaluation.ats_application_id)
            for evaluation in evaluations.values()
            if evaluation.ats_application_id is not None
        }
    )
    ats_by_id: dict[int, CandidateApplication] = {}
    if ats_ids and scope.application_role_id is not None:
        ats_by_id = {
            int(application.id): application
            for application in db.query(CandidateApplication)
            .filter(
                CandidateApplication.id.in_(ats_ids),
                CandidateApplication.organization_id == organization_id,
                CandidateApplication.role_id == int(scope.application_role_id),
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        }

    def _validated_ats_application(
        *,
        application: CandidateApplication,
        evaluation: SisterRoleEvaluation | None,
    ) -> CandidateApplication | None:
        if evaluation is None or evaluation.ats_application_id is None:
            return None
        transport = ats_by_id.get(int(evaluation.ats_application_id))
        if (
            transport is None
            or int(transport.candidate_id) != int(application.candidate_id)
        ):
            return None
        return transport

    return [
        LogicalRoleApplicationContext(
            role=scope.requested_role,
            source_application=application,
            related_evaluation=evaluations.get(int(application.id)),
            ats_application=_validated_ats_application(
                application=application,
                evaluation=evaluations.get(int(application.id)),
            ),
            assessment_score=(
                truth.assessment_score if truth is not None else None
            ),
            assessment_truth=truth,
        )
        for application in applications
        for truth in (
            assessment_truth.get((role_id, int(application.id))),
        )
    ]


def authorize_logical_role_applications(
    db: Session,
    *,
    role: Role,
    application_ids: Iterable[int],
) -> tuple[LogicalRoleApplicationContext, ...]:
    """Authorize an entire application batch before any mutation occurs."""

    requested = _normalized_application_ids(application_ids)
    if not requested:
        return ()
    contexts = _scoped_contexts(db, role=role, application_ids=requested)
    by_id = {context.application_id: context for context in contexts}
    missing = tuple(value for value in requested if value not in by_id)
    if missing:
        raise LogicalRoleApplicationAuthorizationError(
            "One or more applications are not in the acting role's candidate pool.",
            role_id=int(role.id),
            application_ids=missing,
        )
    return tuple(by_id[value] for value in requested)


def list_logical_role_applications(
    db: Session,
    *,
    role: Role,
) -> tuple[LogicalRoleApplicationContext, ...]:
    """Return the complete live pool owned by one logical role.

    Batch operations must use the same membership boundary as selected-item
    operations.  In particular, a related role's roster is its live
    ``SisterRoleEvaluation`` set; the physical application's owner is never a
    substitute for that membership.
    """

    return tuple(_scoped_contexts(db, role=role))


def authorize_logical_role_application(
    db: Session,
    *,
    role: Role,
    application_id: int,
) -> LogicalRoleApplicationContext:
    """Authorize one application against the acting logical role."""

    return authorize_logical_role_applications(
        db,
        role=role,
        application_ids=(int(application_id),),
    )[0]


def authorize_logical_role_action_application(
    db: Session,
    *,
    role: Role,
    application_id: int,
) -> LogicalRoleApplicationContext:
    """Lock and re-authorize one subject at an action side-effect boundary.

    Callers resolve (and, for automatic actions, lock) the acting role first.
    This helper preserves Role -> Application -> related-membership lock order.
    A soft-deleted application may remain evidence for a live related
    membership; the same row remains invalid for its ordinary owner role.

    ATS transport is intentionally not part of membership authority. Missing
    transport can restrict external write-back without erasing the role-owned
    candidate or blocking Taali-owned actions.
    """

    application_query = db.query(CandidateApplication).filter(
        CandidateApplication.id == int(application_id),
        CandidateApplication.organization_id == int(role.organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        application_query = application_query.with_for_update()
    locked_application = application_query.populate_existing().one_or_none()
    if locked_application is None:
        raise LogicalRoleApplicationAuthorizationError(
            "Application is unavailable for this role.",
            role_id=int(role.id),
            application_ids=(int(application_id),),
        )

    context = authorize_logical_role_application(
        db,
        role=role,
        application_id=int(application_id),
    )
    if not context.is_related:
        return replace(context, source_application=locked_application)

    membership_query = db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.organization_id == int(role.organization_id),
        SisterRoleEvaluation.role_id == int(role.id),
        SisterRoleEvaluation.candidate_id == int(locked_application.candidate_id),
        SisterRoleEvaluation.source_application_id == int(locked_application.id),
        SisterRoleEvaluation.deleted_at.is_(None),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        membership_query = membership_query.with_for_update()
    locked_membership = membership_query.populate_existing().one_or_none()
    if locked_membership is None:
        raise LogicalRoleApplicationAuthorizationError(
            "Candidate is no longer in the acting role's candidate pool.",
            role_id=int(role.id),
            application_ids=(int(application_id),),
            candidate_id=int(locked_application.candidate_id),
        )
    return replace(
        context,
        source_application=locked_application,
        related_evaluation=locked_membership,
    )


def authorize_historical_logical_role_application(
    db: Session,
    *,
    role: Role,
    application_id: int,
) -> LogicalRoleApplicationContext:
    """Authorize immutable history for a role that once owned the subject.

    Current-state readers use :func:`authorize_logical_role_application` and
    therefore require live membership. Audit timelines have a different,
    explicit lifecycle: a removed membership leaves the pool but its immutable
    role-attributed events remain readable to an authorized viewer. This helper
    proves the complete tenant/role/candidate/source identity without treating
    an ATS transport link as historical membership authority.
    """

    organization_id = int(role.organization_id)
    source = (
        db.query(CandidateApplication)
        .options(
            joinedload(CandidateApplication.candidate),
            joinedload(CandidateApplication.role),
        )
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == organization_id,
            Candidate.organization_id == organization_id,
        )
        .one_or_none()
    )
    if source is None:
        raise LogicalRoleApplicationAuthorizationError(
            "Application history is unavailable for this role.",
            role_id=int(role.id),
            application_ids=(int(application_id),),
        )

    is_related = bool(
        str(role.role_kind or "") == ROLE_KIND_SISTER
        or role.ats_owner_role_id is not None
    )
    if not is_related:
        if int(source.role_id) != int(role.id):
            raise LogicalRoleApplicationAuthorizationError(
                "Application history is unavailable for this role.",
                role_id=int(role.id),
                application_ids=(int(application_id),),
            )
        return LogicalRoleApplicationContext(role=role, source_application=source)

    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.organization_id == organization_id,
            SisterRoleEvaluation.role_id == int(role.id),
            SisterRoleEvaluation.source_application_id == int(source.id),
            SisterRoleEvaluation.candidate_id == int(source.candidate_id),
        )
        .order_by(
            SisterRoleEvaluation.deleted_at.is_(None).desc(),
            SisterRoleEvaluation.id.desc(),
        )
        .first()
    )
    if evaluation is None:
        raise LogicalRoleApplicationAuthorizationError(
            "Application history is unavailable for this role.",
            role_id=int(role.id),
            application_ids=(int(application_id),),
        )
    assessment_truth = assessment_truth_by_logical_membership(
        db,
        organization_id=organization_id,
        memberships=[(int(role.id), source)],
    ).get((int(role.id), int(source.id)))
    return LogicalRoleApplicationContext(
        role=role,
        source_application=source,
        related_evaluation=evaluation,
        assessment_score=(
            assessment_truth.assessment_score
            if assessment_truth is not None
            else None
        ),
        assessment_truth=assessment_truth,
    )


def authorize_logical_role_candidate(
    db: Session,
    *,
    role: Role,
    candidate_id: int,
) -> LogicalRoleApplicationContext:
    """Resolve a candidate to exactly one live membership in the acting role."""

    contexts = _scoped_contexts(
        db,
        role=role,
        candidate_id=int(candidate_id),
    )
    if len(contexts) != 1:
        raise LogicalRoleApplicationAuthorizationError(
            "Candidate is not in the acting role's candidate pool.",
            role_id=int(role.id),
            candidate_id=int(candidate_id),
        )
    return contexts[0]


__all__ = [
    "LogicalRoleApplicationAuthorizationError",
    "LogicalRoleApplicationContext",
    "authorize_logical_role_action_application",
    "authorize_logical_role_application",
    "authorize_logical_role_applications",
    "authorize_logical_role_candidate",
    "authorize_historical_logical_role_application",
    "list_logical_role_applications",
]
