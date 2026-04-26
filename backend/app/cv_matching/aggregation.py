"""Deterministic aggregation over LLM-emitted CV match data.

Source of truth: ``backend/app/cv_matching/calibration.md``.

The LLM produces only ``skills_match_score``, ``experience_relevance_score``,
and per-requirement assessments. Everything else is derived here. This split
eliminates LLM variance on multi-factor weighted arithmetic and makes scores
auditable.

Functions are pure: same inputs always produce same outputs. Unit-tested in
``backend/tests/test_cv_matching_aggregation.py`` against the worked example
and edge cases.
"""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import Priority, Recommendation, RequirementAssessment, Status

# Priority weights for non-constraint requirements. Constraints are handled
# separately via floors below.
_PRIORITY_WEIGHTS: dict[Priority, float] = {
    Priority.MUST_HAVE: 0.70,
    Priority.STRONG_PREFERENCE: 0.25,
    Priority.NICE_TO_HAVE: 0.05,
}

# Status multipliers applied to priority weights.
_STATUS_WEIGHTS: dict[Status, float] = {
    Status.MET: 1.0,
    Status.PARTIALLY_MET: 0.5,
    Status.UNKNOWN: 0.3,
    Status.MISSING: 0.0,
}

# Floor caps when disqualifying requirements/constraints fail.
_CONSTRAINT_FLOOR = 30.0
_MUST_HAVE_FLOOR = 40.0

# Recommendation thresholds on role_fit_score.
_STRONG_YES_THRESHOLD = 85.0
_YES_THRESHOLD = 70.0
_LEAN_NO_THRESHOLD = 50.0


def _is_unfulfilled(status: Status) -> bool:
    return status in (Status.MISSING, Status.UNKNOWN)


def compute_requirements_match_score(
    assessments: Iterable[RequirementAssessment],
) -> float:
    """Weighted average across requirements with priority+status weights.

    Algorithm (from calibration.md):
      1. total_weight = Σ priority_weight over non-constraint requirements
      2. earned_weight = Σ (priority_weight × status_weight) over same set
      3. base = (earned_weight / total_weight) × 100
      4. apply caps: if any disqualifying constraint missing/unknown → cap at 30;
         if any disqualifying must_have missing/unknown → cap at 40

    Edge: if no non-constraint requirements (or total_weight is 0), return 50.0.
    """
    assessments_list = list(assessments)

    total_weight = 0.0
    earned_weight = 0.0
    for a in assessments_list:
        if a.priority == Priority.CONSTRAINT:
            continue
        priority_weight = _PRIORITY_WEIGHTS.get(a.priority, 0.0)
        status_weight = _STATUS_WEIGHTS.get(a.status, 0.0)
        total_weight += priority_weight
        earned_weight += priority_weight * status_weight

    if total_weight <= 0:
        return 50.0

    score = (earned_weight / total_weight) * 100.0

    # Floors are caps from above when disqualifying gates fail.
    has_failed_disq_constraint = any(
        a.priority == Priority.CONSTRAINT
        and _is_unfulfilled(a.status)
        for a in assessments_list
    )
    has_failed_disq_must_have = any(
        a.priority == Priority.MUST_HAVE and _is_unfulfilled(a.status)
        for a in assessments_list
    )

    if has_failed_disq_constraint:
        score = min(score, _CONSTRAINT_FLOOR)
    if has_failed_disq_must_have:
        score = min(score, _MUST_HAVE_FLOOR)

    return round(score, 2)


def compute_cv_fit(
    skills_match_score: float, experience_relevance_score: float
) -> float:
    """Simple average of the two LLM-produced sub-scores."""
    return round((skills_match_score + experience_relevance_score) / 2.0, 2)


def compute_role_fit(cv_fit: float, requirements_match: float) -> float:
    """role_fit = 0.40 × cv_fit + 0.60 × requirements_match.

    Note: this is the cv_match_v3.0 weighting per ``calibration.md``. The
    legacy ``backend/app/services/taali_scoring.py`` uses 50/50 — that path
    is unchanged.
    """
    return round(0.40 * cv_fit + 0.60 * requirements_match, 2)


def derive_recommendation(
    role_fit: float,
    *,
    has_failed_constraint: bool,
    has_missing_must_have: bool,
) -> Recommendation:
    """Hard rules first (constraint failures), then score thresholds.

    Args:
        role_fit: aggregated role-fit score, 0-100.
        has_failed_constraint: any disqualifying constraint missing/unknown.
        has_missing_must_have: any must_have missing/unknown (regardless of
            disqualifying flag — the calibration says any missing must_have
            caps the recommendation at LEAN_NO).
    """
    if has_failed_constraint:
        return Recommendation.NO

    if has_missing_must_have:
        # Cap at LEAN_NO. If the score would otherwise yield NO, leave NO.
        if role_fit < _LEAN_NO_THRESHOLD:
            return Recommendation.NO
        return Recommendation.LEAN_NO

    if role_fit >= _STRONG_YES_THRESHOLD:
        return Recommendation.STRONG_YES
    if role_fit >= _YES_THRESHOLD:
        return Recommendation.YES
    if role_fit >= _LEAN_NO_THRESHOLD:
        return Recommendation.LEAN_NO
    return Recommendation.NO


def aggregate(
    *,
    skills_match_score: float,
    experience_relevance_score: float,
    assessments: Iterable[RequirementAssessment],
) -> tuple[float, float, float, Recommendation]:
    """Run the full aggregation chain.

    Returns (requirements_match, cv_fit, role_fit, recommendation).
    """
    assessments_list = list(assessments)
    requirements_match = compute_requirements_match_score(assessments_list)
    cv_fit = compute_cv_fit(skills_match_score, experience_relevance_score)
    role_fit = compute_role_fit(cv_fit, requirements_match)

    has_failed_constraint = any(
        a.priority == Priority.CONSTRAINT and _is_unfulfilled(a.status)
        for a in assessments_list
    )
    has_missing_must_have = any(
        a.priority == Priority.MUST_HAVE and _is_unfulfilled(a.status)
        for a in assessments_list
    )

    recommendation = derive_recommendation(
        role_fit,
        has_failed_constraint=has_failed_constraint,
        has_missing_must_have=has_missing_must_have,
    )
    return requirements_match, cv_fit, role_fit, recommendation
