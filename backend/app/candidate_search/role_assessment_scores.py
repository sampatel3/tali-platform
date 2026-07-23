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

from sqlalchemy import case, func, literal, select
from sqlalchemy.orm import Session

from ..models.assessment import Assessment, AssessmentStatus
from ..models.candidate_application import CandidateApplication
from ..services.taali_scoring import TAALI_WEIGHTS, compute_taali_score


_COMPLETED_STATUSES = (
    AssessmentStatus.COMPLETED,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
)


def assessment_score_100(assessment: Assessment | None) -> float | None:
    """Return the completed technical-assessment score on a 0-100 scale."""

    if assessment is None:
        return None
    status = getattr(assessment.status, "value", assessment.status)
    if status not in {item.value for item in _COMPLETED_STATUSES}:
        return None
    if bool(assessment.scoring_partial) or bool(assessment.scoring_failed):
        return None
    for value, multiplier in (
        (assessment.assessment_score, 1.0),
        (assessment.final_score, 1.0),
        (assessment.score, 10.0),
    ):
        if value is None:
            continue
        try:
            numeric = float(value) * multiplier
        except (TypeError, ValueError):
            continue
        if numeric < 0:
            continue
        return round(max(0.0, min(100.0, numeric)), 1)
    return None


def _normalized_column(column: Any, *, multiplier: float = 1.0) -> Any:
    value = column * multiplier
    return case(
        (column.is_(None), literal(None)),
        (value < 0, literal(None)),
        (value > 100, literal(100.0)),
        else_=value,
    )


def assessment_score_value_expression() -> Any:
    """Portable 0-100 technical score expression over ``Assessment``."""

    return func.coalesce(
        _normalized_column(Assessment.assessment_score),
        _normalized_column(Assessment.final_score),
        _normalized_column(Assessment.score, multiplier=10.0),
    )


def blended_taali_score_expression(
    *, assessment_expression: Any, role_fit_expression: Any
) -> Any:
    """SQL equivalent of ``compute_taali_score`` for role-owned inputs."""

    assessment_weight = float(TAALI_WEIGHTS["assessment"])
    role_fit_weight = float(TAALI_WEIGHTS["role_fit"])
    denominator = assessment_weight + role_fit_weight
    blended = (
        assessment_expression * assessment_weight
        + role_fit_expression * role_fit_weight
    ) / denominator
    return case(
        (
            assessment_expression.isnot(None) & role_fit_expression.isnot(None),
            blended,
        ),
        (assessment_expression.isnot(None), assessment_expression),
        else_=role_fit_expression,
    )


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
            Assessment.scoring_partial.is_not(True),
            Assessment.scoring_failed.is_not(True),
        )
        .order_by(
            Assessment.completed_at.desc(),
            Assessment.created_at.desc(),
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
    """Blend this role's assessment and fit signals with normal fallbacks."""

    assessment = related_assessment_score_expression(
        organization_id=organization_id,
        role_id=role_id,
        correlate_froms=correlate_froms,
    )
    return blended_taali_score_expression(
        assessment_expression=assessment,
        role_fit_expression=role_fit_expression,
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
            Assessment.completed_at.desc(),
            Assessment.created_at.desc(),
            Assessment.id.desc(),
        )
        .all()
    )
    score_by_candidate: dict[int, float] = {}
    for assessment in rows:
        candidate_id = int(assessment.candidate_id)
        if candidate_id in score_by_candidate:
            continue
        score = assessment_score_100(assessment)
        if score is not None:
            score_by_candidate[candidate_id] = score

    return {
        application_id: score_by_candidate[candidate_id]
        for candidate_id, application_ids in candidate_to_application_ids.items()
        if candidate_id in score_by_candidate
        for application_id in application_ids
    }


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


def related_taali_score(
    *, assessment_score: float | None, role_fit_score: float | None
) -> float | None:
    """Python projection matching :func:`related_taali_score_expression`."""

    return compute_taali_score(assessment_score, role_fit_score)


__all__ = [
    "assessment_score_100",
    "assessment_score_value_expression",
    "assessment_scores_by_application",
    "assessment_scores_by_logical_membership",
    "blended_taali_score_expression",
    "related_assessment_score_expression",
    "related_taali_score",
    "related_taali_score_expression",
]
