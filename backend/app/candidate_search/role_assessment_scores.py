"""Role-owned assessment scores for logical candidate searches.

An assessment belongs to ``(organization, role, candidate)``.  Its physical
``application_id`` may point at an evidence/transport row, so related-role
searches must never read the ATS owner's cached application score.  This
module provides the one SQL expression used for filtering/sorting and the one
batched Python projection used for response payloads.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import set_committed_value

from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from .assessment_score_truth import (
    RoleAssessmentTruth,
    assessment_score_100,
    assessment_score_value_expression,
    assessment_snapshot_role_fit_score_100,
    assessment_snapshot_role_fit_value_expression,
    assessment_taali_score_100,
    assessment_taali_score_value_expression,
    blended_taali_score_expression,
    canonical_score_100,
    normalized_score_expression,
    related_taali_score,
    role_assessment_truth,
)


_COMPLETED_STATUSES = (
    AssessmentStatus.COMPLETED,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
)


def hydrate_ordinary_assessment_runtime(
    db: Session,
    *,
    organization_id: int,
    applications: Sequence[CandidateApplication],
) -> None:
    """Attach assessment history by logical role + candidate identity.

    ``Assessment.application_id`` is evidence/transport metadata. A related
    assessment may therefore point at an ordinary ATS application without
    belonging to that ordinary role. Runtime serializers consume the ORM
    relationship, so hydrate it from the canonical logical identity instead
    of the physical foreign key.
    """

    applications_by_identity: dict[
        tuple[int, int], dict[int, CandidateApplication]
    ] = {}
    for application in applications:
        if int(application.organization_id) != int(organization_id):
            raise ValueError(
                "Assessment runtime application belongs to another organization"
            )
        applications_by_identity.setdefault(
            (int(application.role_id), int(application.candidate_id)),
            {},
        )[int(application.id)] = application
    if not applications_by_identity:
        return

    rows = (
        db.query(Assessment)
        .options(joinedload(Assessment.task))
        .filter(
            Assessment.organization_id == int(organization_id),
            tuple_(Assessment.role_id, Assessment.candidate_id).in_(
                sorted(applications_by_identity)
            ),
        )
        .order_by(
            Assessment.role_id.asc(),
            Assessment.candidate_id.asc(),
            Assessment.completed_at.desc().nullslast(),
            Assessment.created_at.desc().nullslast(),
            Assessment.id.desc(),
        )
        .all()
    )
    rows_by_identity: dict[tuple[int, int], list[Assessment]] = {}
    for assessment in rows:
        identity = (int(assessment.role_id), int(assessment.candidate_id))
        if identity in applications_by_identity:
            rows_by_identity.setdefault(identity, []).append(assessment)

    for identity, applications_by_id in applications_by_identity.items():
        runtime_rows = rows_by_identity.get(identity, [])
        for application in applications_by_id.values():
            set_committed_value(application, "assessments", runtime_rows)


def related_assessment_score_expression(
    *,
    organization_id: int,
    role_id: Any,
    correlate_froms: Sequence[Any] | None = None,
) -> Any:
    """Correlated role-local technical score for ``CandidateApplication``."""

    score = assessment_score_value_expression()
    return (
        select(score)
        .where(
            Assessment.organization_id == int(organization_id),
            Assessment.role_id
            == (int(role_id) if isinstance(role_id, int) else role_id),
            Assessment.candidate_id == CandidateApplication.candidate_id,
            Assessment.is_voided.is_(False),
            Assessment.status.in_(_COMPLETED_STATUSES),
        )
        .order_by(
            Assessment.completed_at.desc().nullslast(),
            Assessment.created_at.desc().nullslast(),
            Assessment.id.desc(),
        )
        .limit(1)
        .correlate(*(correlate_froms or (CandidateApplication,)))
        .scalar_subquery()
    )


def related_taali_score_expression(
    *,
    organization_id: int,
    role_id: Any,
    role_fit_expression: Any,
    correlate_froms: Sequence[Any] | None = None,
) -> Any:
    """Return the canonical frozen TAALI score for this logical candidate.

    A completed assessment owns the score even when the value is unavailable
    because grading is partial/failed. Only candidates with no completed
    assessment fall back to their current related-role fit.
    """

    normalized_role_id = int(role_id) if isinstance(role_id, int) else role_id
    filters = (
        Assessment.organization_id == int(organization_id),
        Assessment.role_id == normalized_role_id,
        Assessment.candidate_id == CandidateApplication.candidate_id,
        Assessment.is_voided.is_(False),
        Assessment.status.in_(_COMPLETED_STATUSES),
    )
    ordering = (
        Assessment.completed_at.desc().nullslast(),
        Assessment.created_at.desc().nullslast(),
        Assessment.id.desc(),
    )
    correlations = correlate_froms or (CandidateApplication,)
    latest_assessment_id = (
        select(Assessment.id)
        .where(*filters)
        .order_by(*ordering)
        .limit(1)
        .correlate(*correlations)
        .scalar_subquery()
    )
    # Compose legacy TAALI from staged columns. Referencing the full canonical
    # expression inline causes SQLAlchemy to duplicate its nested normalization
    # and JSON CASE tree throughout the scalar subquery, overflowing older
    # SQLite parsers used by CI.
    latest_inputs = (
        select(
            Assessment.scoring_partial.label("scoring_partial"),
            Assessment.scoring_failed.label("scoring_failed"),
            normalized_score_expression(Assessment.taali_score).label(
                "persisted_taali_score"
            ),
            assessment_score_value_expression().label("assessment_score"),
            assessment_snapshot_role_fit_value_expression().label(
                "snapshot_role_fit_score"
            ),
        )
        .where(*filters)
        .order_by(*ordering)
        .limit(1)
        .correlate(*correlations)
        .subquery("latest_related_assessment_inputs")
    )
    legacy_taali_score = blended_taali_score_expression(
        assessment_expression=latest_inputs.c.assessment_score,
        role_fit_expression=latest_inputs.c.snapshot_role_fit_score,
    )
    latest_taali_score = (
        select(
            case(
                (
                    latest_inputs.c.scoring_partial.is_(True)
                    | latest_inputs.c.scoring_failed.is_(True),
                    None,
                ),
                else_=func.coalesce(
                    latest_inputs.c.persisted_taali_score,
                    legacy_taali_score,
                ),
            )
        )
        .select_from(latest_inputs)
        .scalar_subquery()
    )
    return case(
        (latest_assessment_id.isnot(None), latest_taali_score),
        else_=normalized_score_expression(role_fit_expression),
    )


def assessment_scores_by_application(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    applications: Sequence[CandidateApplication],
) -> dict[int, float]:
    """Batch-load role-owned scores keyed by the presented application id."""

    candidate_to_application_ids: dict[int, list[int]] = {}
    for application in applications:
        candidate_to_application_ids.setdefault(
            int(application.candidate_id), []
        ).append(int(application.id))
    if not candidate_to_application_ids:
        return {}

    rows = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == int(organization_id),
            Assessment.role_id == int(role_id),
            Assessment.candidate_id.in_(sorted(candidate_to_application_ids)),
            Assessment.is_voided.is_(False),
            Assessment.status.in_(_COMPLETED_STATUSES),
        )
        .order_by(
            Assessment.candidate_id.asc(),
            Assessment.completed_at.desc().nullslast(),
            Assessment.created_at.desc().nullslast(),
            Assessment.id.desc(),
        )
        .all()
    )
    score_by_candidate: dict[int, float] = {}
    seen_candidates: set[int] = set()
    for assessment in rows:
        candidate_id = int(assessment.candidate_id)
        if candidate_id in seen_candidates:
            continue
        seen_candidates.add(candidate_id)
        score = assessment_score_100(assessment)
        if score is not None:
            score_by_candidate[candidate_id] = score

    return {
        application_id: score_by_candidate[candidate_id]
        for candidate_id, application_ids in candidate_to_application_ids.items()
        if candidate_id in score_by_candidate
        for application_id in application_ids
    }


def assessment_truth_by_application(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    applications: Sequence[CandidateApplication],
) -> dict[int, RoleAssessmentTruth]:
    """Batch-load canonical completed truth keyed by presented application id."""

    candidate_to_application_ids: dict[int, list[int]] = {}
    for application in applications:
        candidate_to_application_ids.setdefault(
            int(application.candidate_id), []
        ).append(int(application.id))
    if not candidate_to_application_ids:
        return {}

    rows = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == int(organization_id),
            Assessment.role_id == int(role_id),
            Assessment.candidate_id.in_(sorted(candidate_to_application_ids)),
            Assessment.is_voided.is_(False),
            Assessment.status.in_(_COMPLETED_STATUSES),
        )
        .order_by(
            Assessment.candidate_id.asc(),
            Assessment.completed_at.desc().nullslast(),
            Assessment.created_at.desc().nullslast(),
            Assessment.id.desc(),
        )
        .all()
    )
    truth_by_candidate: dict[int, RoleAssessmentTruth] = {}
    for assessment in rows:
        candidate_id = int(assessment.candidate_id)
        if candidate_id in truth_by_candidate:
            continue
        truth_by_candidate[candidate_id] = role_assessment_truth(assessment)

    return {
        application_id: truth_by_candidate[candidate_id]
        for candidate_id, application_ids in candidate_to_application_ids.items()
        if candidate_id in truth_by_candidate
        for application_id in application_ids
    }


def assessment_truth_by_logical_membership(
    db: Session,
    *,
    organization_id: int,
    memberships: Sequence[tuple[int, CandidateApplication]],
) -> dict[tuple[int, int], RoleAssessmentTruth]:
    """Batch-load canonical truth for independent logical memberships."""

    applications_by_role: dict[int, dict[int, CandidateApplication]] = {}
    for role_id, application in memberships:
        applications_by_role.setdefault(int(role_id), {})[
            int(application.id)
        ] = application

    result: dict[tuple[int, int], RoleAssessmentTruth] = {}
    for role_id, applications_by_id in applications_by_role.items():
        truth = assessment_truth_by_application(
            db,
            organization_id=int(organization_id),
            role_id=role_id,
            applications=list(applications_by_id.values()),
        )
        result.update(
            {
                (role_id, int(application_id)): scores
                for application_id, scores in truth.items()
            }
        )
    return result


def assessment_scores_by_logical_membership(
    db: Session,
    *,
    organization_id: int,
    memberships: Sequence[tuple[int, CandidateApplication]],
) -> dict[tuple[int, int], float]:
    """Batch-load scores keyed by ``(logical_role_id, application_id)``.

    A physical application can be an ordinary-role membership and evidence for
    multiple independent related roles. Keying only by ``application_id``
    would collapse those different assessment truths, so profile and
    authorization projections use the composite logical identity.
    """

    applications_by_role: dict[int, dict[int, CandidateApplication]] = {}
    for role_id, application in memberships:
        applications_by_role.setdefault(int(role_id), {})[
            int(application.id)
        ] = application

    result: dict[tuple[int, int], float] = {}
    for role_id, applications_by_id in applications_by_role.items():
        scores = assessment_scores_by_application(
            db,
            organization_id=int(organization_id),
            role_id=role_id,
            applications=list(applications_by_id.values()),
        )
        result.update(
            {
                (role_id, int(application_id)): score
                for application_id, score in scores.items()
            }
        )
    return result


__all__ = [
    "RoleAssessmentTruth",
    "assessment_score_100",
    "assessment_score_value_expression",
    "assessment_scores_by_application",
    "assessment_scores_by_logical_membership",
    "assessment_snapshot_role_fit_score_100",
    "assessment_taali_score_100",
    "assessment_taali_score_value_expression",
    "assessment_truth_by_application",
    "assessment_truth_by_logical_membership",
    "blended_taali_score_expression",
    "canonical_score_100",
    "hydrate_ordinary_assessment_runtime",
    "normalized_score_expression",
    "related_assessment_score_expression",
    "related_taali_score",
    "related_taali_score_expression",
]
