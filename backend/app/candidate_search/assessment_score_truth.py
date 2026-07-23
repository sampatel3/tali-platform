"""Canonical frozen assessment scores shared by SQL and Python projections."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Any

from sqlalchemy import Numeric, and_, case, cast, func, literal
from sqlalchemy.orm import Session

from ..models.assessment import Assessment, AssessmentStatus
from ..services.taali_scoring import ROLE_FIT_WEIGHTS, TAALI_WEIGHTS


_COMPLETED_STATUSES = (
    AssessmentStatus.COMPLETED,
    AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
)


@dataclass(frozen=True)
class RoleAssessmentTruth:
    """Canonical truth frozen on the latest completed role assessment.

    The grading fields are deliberately carried with the headline scores.
    A terminal assessment whose rubric grading is partial or failed is a
    distinct lifecycle state, not a role-fit-only result and not a completed
    assessment score.
    """

    assessment_id: int
    status: str
    assessment_score: float | None
    taali_score: float | None
    score_mode: str
    grading_state: str
    scoring_partial: bool
    scoring_failed: bool

    @property
    def grading_pending(self) -> bool:
        return self.grading_state in {"partial", "failed"}


def _assessment_status(assessment: Assessment | None) -> str:
    return str(
        getattr(getattr(assessment, "status", None), "value", None)
        or getattr(assessment, "status", "")
    ).strip().lower()


def assessment_grading_state(assessment: Assessment | None) -> str:
    """Return the persisted rubric grading state for one completed attempt."""

    if assessment is None:
        return "unavailable"
    if bool(getattr(assessment, "scoring_failed", False)):
        return "failed"
    if bool(getattr(assessment, "scoring_partial", False)):
        return "partial"
    return "complete"


def assessment_score_mode(assessment: Assessment | None) -> str:
    """Return the canonical candidate-facing mode for one completed attempt."""

    if assessment_grading_state(assessment) in {"partial", "failed"}:
        return "rubric_grading_pending"
    if assessment_snapshot_role_fit_score_100(assessment) is not None:
        return "assessment_plus_role_fit"
    return "assessment_only_fallback"


def role_assessment_truth(
    assessment: Assessment,
) -> RoleAssessmentTruth:
    """Project one completed assessment through the shared frozen-score rules."""

    partial = bool(getattr(assessment, "scoring_partial", False))
    failed = bool(getattr(assessment, "scoring_failed", False))
    return RoleAssessmentTruth(
        assessment_id=int(assessment.id),
        status=_assessment_status(assessment),
        assessment_score=assessment_score_100(assessment),
        taali_score=assessment_taali_score_100(assessment),
        score_mode=assessment_score_mode(assessment),
        grading_state=assessment_grading_state(assessment),
        scoring_partial=partial,
        scoring_failed=failed,
    )


def latest_role_assessment(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    candidate_id: int,
    completed_only: bool = False,
) -> Assessment | None:
    """Resolve an assessment by its logical identity, never its transport row."""

    query = db.query(Assessment).filter(
        Assessment.organization_id == int(organization_id),
        Assessment.role_id == int(role_id),
        Assessment.candidate_id == int(candidate_id),
        Assessment.is_voided.is_(False),
    )
    if completed_only:
        query = query.filter(Assessment.status.in_(_COMPLETED_STATUSES))
        ordering = (
            Assessment.completed_at.desc().nullslast(),
            Assessment.created_at.desc().nullslast(),
            Assessment.id.desc(),
        )
    else:
        ordering = (
            Assessment.created_at.desc().nullslast(),
            Assessment.id.desc(),
        )
    return (
        query.order_by(*ordering)
        .first()
    )


def latest_completed_role_assessment(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    candidate_id: int,
) -> Assessment | None:
    """Return the latest completed assessment for one logical membership."""

    return latest_role_assessment(
        db,
        organization_id=organization_id,
        role_id=role_id,
        candidate_id=candidate_id,
        completed_only=True,
    )


def canonical_score_100(value: Any) -> float | None:
    """Normalize a score with deterministic decimal half-up rounding."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(numeric) or numeric < 0:
        return None
    try:
        decimal_value = Decimal(str(min(100.0, numeric)))
        return float(
            decimal_value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, ValueError):
        return None


def _weighted_score_100(
    *weighted_values: tuple[float | None, float],
) -> float | None:
    numerator = Decimal("0")
    denominator = Decimal("0")
    for value, weight in weighted_values:
        normalized = canonical_score_100(value)
        try:
            decimal_weight = Decimal(str(float(weight)))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if normalized is None or decimal_weight <= 0:
            continue
        numerator += Decimal(str(normalized)) * decimal_weight
        denominator += decimal_weight
    if denominator <= 0:
        return None
    return canonical_score_100(numerator / denominator)


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
        normalized = canonical_score_100(numeric)
        if normalized is not None:
            return normalized
    return None


def normalized_score_expression(column: Any, *, multiplier: float = 1.0) -> Any:
    """Portable SQL equivalent of :func:`canonical_score_100`."""

    value = column * multiplier
    rounded = func.round(cast(value, Numeric(20, 8)), 1)
    return case(
        (value < 0, literal(None)),
        (value > 100, literal(100.0)),
        else_=rounded,
    )


def assessment_score_value_expression() -> Any:
    """Portable 0-100 technical score expression over ``Assessment``."""

    score = func.coalesce(
        normalized_score_expression(Assessment.assessment_score),
        normalized_score_expression(Assessment.final_score),
        normalized_score_expression(Assessment.score, multiplier=10.0),
    )
    return case(
        (
            Assessment.scoring_partial.is_(True)
            | Assessment.scoring_failed.is_(True),
            literal(None),
        ),
        else_=score,
    )


def _weighted_score_expression(
    *weighted_values: tuple[Any, float],
    values_are_normalized: bool = False,
) -> Any:
    normalized = [
        (
            value if values_are_normalized else normalized_score_expression(value),
            float(weight),
        )
        for value, weight in weighted_values
    ]
    numerator = sum(
        (value * weight for value, weight in normalized),
        literal(0.0),
    )
    denominator = sum(
        (
            case((value.isnot(None), literal(weight)), else_=literal(0.0))
            for value, weight in normalized
        ),
        literal(0.0),
    )
    return case(
        (
            denominator > 0,
            func.round(cast(numerator / denominator, Numeric(20, 8)), 1),
        ),
        else_=literal(None),
    )


def blended_taali_score_expression(
    *, assessment_expression: Any, role_fit_expression: Any
) -> Any:
    """Blend normalized role-owned score expressions."""

    return _weighted_score_expression(
        (assessment_expression, float(TAALI_WEIGHTS["assessment"])),
        (role_fit_expression, float(TAALI_WEIGHTS["role_fit"])),
    )


def assessment_snapshot_role_fit_value_expression() -> Any:
    """Role-fit snapshot used by legacy assessments without persisted TAALI."""

    component = Assessment.score_breakdown["score_components"][
        "role_fit_score"
    ].as_float()
    component_score = normalized_score_expression(component)

    details = Assessment.cv_job_match_details
    scale = func.lower(
        func.coalesce(details["score_scale"].as_string(), literal(""))
    )
    raw_cv_score = case(
        (
            and_(scale.contains("10"), ~scale.contains("100")),
            Assessment.cv_job_match_score * 10.0,
        ),
        else_=Assessment.cv_job_match_score,
    )
    derived_role_fit = _weighted_score_expression(
        (normalized_score_expression(raw_cv_score), float(ROLE_FIT_WEIGHTS["cv_fit"])),
        (
            normalized_score_expression(
                details["requirements_match_score_100"].as_float()
            ),
            float(ROLE_FIT_WEIGHTS["requirements_fit"]),
        ),
        values_are_normalized=True,
    )
    return func.coalesce(component_score, derived_role_fit)


def assessment_snapshot_role_fit_score_100(
    assessment: Assessment | None,
) -> float | None:
    """Return the role-fit score frozen with an assessment attempt."""

    if assessment is None:
        return None
    score_breakdown = (
        assessment.score_breakdown
        if isinstance(getattr(assessment, "score_breakdown", None), dict)
        else {}
    )
    score_components = score_breakdown.get("score_components")
    if isinstance(score_components, dict):
        component_score = canonical_score_100(
            score_components.get("role_fit_score")
        )
        if component_score is not None:
            return component_score

    details = (
        assessment.cv_job_match_details
        if isinstance(getattr(assessment, "cv_job_match_details", None), dict)
        else {}
    )
    cv_fit_raw = getattr(assessment, "cv_job_match_score", None)
    scale = str(details.get("score_scale") or "").strip().lower()
    if "10" in scale and "100" not in scale:
        try:
            cv_fit_raw = float(cv_fit_raw) * 10.0
        except (TypeError, ValueError):
            cv_fit_raw = None
    return _weighted_score_100(
        (canonical_score_100(cv_fit_raw), float(ROLE_FIT_WEIGHTS["cv_fit"])),
        (
            canonical_score_100(details.get("requirements_match_score_100")),
            float(ROLE_FIT_WEIGHTS["requirements_fit"]),
        ),
    )


def assessment_taali_score_100(assessment: Assessment | None) -> float | None:
    """Use persisted TAALI, then only the same attempt's frozen inputs."""

    if assessment is None:
        return None
    status = getattr(assessment.status, "value", assessment.status)
    if status not in {item.value for item in _COMPLETED_STATUSES}:
        return None
    if bool(assessment.scoring_partial) or bool(assessment.scoring_failed):
        return None
    persisted = canonical_score_100(getattr(assessment, "taali_score", None))
    if persisted is not None:
        return persisted
    return _weighted_score_100(
        (assessment_score_100(assessment), float(TAALI_WEIGHTS["assessment"])),
        (
            assessment_snapshot_role_fit_score_100(assessment),
            float(TAALI_WEIGHTS["role_fit"]),
        ),
    )


def assessment_taali_score_value_expression() -> Any:
    """SQL equivalent of :func:`assessment_taali_score_100` for one row."""

    persisted = normalized_score_expression(Assessment.taali_score)
    legacy = blended_taali_score_expression(
        assessment_expression=assessment_score_value_expression(),
        role_fit_expression=assessment_snapshot_role_fit_value_expression(),
    )
    return case(
        (
            Assessment.scoring_partial.is_(True)
            | Assessment.scoring_failed.is_(True),
            literal(None),
        ),
        else_=func.coalesce(persisted, legacy),
    )


def related_taali_score(
    *, assessment_score: float | None, role_fit_score: float | None
) -> float | None:
    """Compatibility blend for callers without completed-attempt truth."""

    return _weighted_score_100(
        (assessment_score, float(TAALI_WEIGHTS["assessment"])),
        (role_fit_score, float(TAALI_WEIGHTS["role_fit"])),
    )


__all__ = [
    "RoleAssessmentTruth",
    "assessment_grading_state",
    "assessment_score_100",
    "assessment_score_mode",
    "assessment_score_value_expression",
    "assessment_snapshot_role_fit_score_100",
    "assessment_snapshot_role_fit_value_expression",
    "assessment_taali_score_100",
    "assessment_taali_score_value_expression",
    "blended_taali_score_expression",
    "canonical_score_100",
    "latest_completed_role_assessment",
    "latest_role_assessment",
    "normalized_score_expression",
    "related_taali_score",
    "role_assessment_truth",
]
