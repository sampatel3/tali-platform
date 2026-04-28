"""Deterministic aggregation over LLM-emitted CV match data.

The LLM produces six dimension scores + per-requirement assessments.
Everything else is derived here.
"""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import (
    DimensionScores,
    Priority,
    Recommendation,
    RequirementAssessment,
    Status,
)

_PRIORITY_WEIGHTS: dict[Priority, float] = {
    Priority.MUST_HAVE: 0.70,
    Priority.STRONG_PREFERENCE: 0.25,
    Priority.NICE_TO_HAVE: 0.05,
}

_STATUS_WEIGHTS: dict[Status, float] = {
    Status.MET: 1.0,
    Status.PARTIALLY_MET: 0.5,
    Status.UNKNOWN: 0.3,
    Status.MISSING: 0.0,
}

_TIER_WEIGHTS: dict[str, float] = {
    "exact": 1.0,
    "strong_substitute": 0.85,
    "weak_substitute": 0.55,
    "unrelated": 0.0,
    "missing": 0.0,
}

_CONSTRAINT_FLOOR = 30.0
_MUST_HAVE_FLOOR = 40.0

_STRONG_YES_THRESHOLD = 85.0
_YES_THRESHOLD = 70.0
_LEAN_NO_THRESHOLD = 50.0


_DEFAULT_DIMENSION_WEIGHTS = {
    "skills_coverage": 0.25,
    "skills_depth": 0.20,
    "title_trajectory": 0.15,
    "seniority_alignment": 0.15,
    "industry_match": 0.15,
    "tenure_pattern": 0.10,
}


def _is_unfulfilled(status: Status) -> bool:
    return status in (Status.MISSING, Status.UNKNOWN)


def _tier_multiplier(assessment) -> float:
    return _TIER_WEIGHTS.get(getattr(assessment, "match_tier", "exact"), 1.0)


def compute_requirements_match_score(
    assessments: Iterable[RequirementAssessment],
) -> float:
    """Weighted average across requirements with priority × status × tier weights.

    Algorithm:
      1. total_weight = Σ priority_weight over non-constraint requirements
      2. earned_weight = Σ (priority_weight × status_weight × tier_weight) over same set
      3. base = (earned_weight / total_weight) × 100
      4. apply caps: disqualifying constraint missing → ≤ 30; disqualifying
         must_have missing → ≤ 40

    Edge: if no non-constraint requirements (or total_weight == 0), return 50.0.
    """
    assessments_list = list(assessments)

    total_weight = 0.0
    earned_weight = 0.0
    for a in assessments_list:
        if a.priority == Priority.CONSTRAINT:
            continue
        priority_weight = _PRIORITY_WEIGHTS.get(a.priority, 0.0)
        status_weight = _STATUS_WEIGHTS.get(a.status, 0.0)
        tier_multiplier = _tier_multiplier(a)
        total_weight += priority_weight
        earned_weight += priority_weight * status_weight * tier_multiplier

    if total_weight <= 0:
        return 50.0

    score = (earned_weight / total_weight) * 100.0

    has_failed_disq_constraint = any(
        a.priority == Priority.CONSTRAINT and _is_unfulfilled(a.status)
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
    """Legacy two-arg cv_fit (simple average). Kept for backwards-compatible
    callers; the dimension-driven version below is the active path."""
    return round((skills_match_score + experience_relevance_score) / 2.0, 2)


def compute_cv_fit_from_dimensions(
    dimension_scores: DimensionScores | None,
    weights: dict[str, float] | None = None,
) -> float:
    """Six-dimension weighted average for cv_fit. None → 0.0."""
    if dimension_scores is None:
        return 0.0
    base_weights = {**_DEFAULT_DIMENSION_WEIGHTS, **(weights or {})}
    total = sum(base_weights.values()) or 1.0
    weighted = (
        base_weights["skills_coverage"] * dimension_scores.skills_coverage
        + base_weights["skills_depth"] * dimension_scores.skills_depth
        + base_weights["title_trajectory"] * dimension_scores.title_trajectory
        + base_weights["seniority_alignment"] * dimension_scores.seniority_alignment
        + base_weights["industry_match"] * dimension_scores.industry_match
        + base_weights["tenure_pattern"] * dimension_scores.tenure_pattern
    )
    return round(weighted / total, 2)


def derive_v3_compat_scores(
    dimension_scores: DimensionScores | None,
) -> tuple[float, float]:
    """Project six dimensions back onto the legacy (skills, experience) pair.

    skills_match_score          = mean(skills_coverage, skills_depth)
    experience_relevance_score  = mean(title_trajectory, seniority_alignment,
                                       industry_match, tenure_pattern)

    Kept so existing consumers (``role_support.py`` etc.) continue to work.
    """
    if dimension_scores is None:
        return 0.0, 0.0
    skills = (dimension_scores.skills_coverage + dimension_scores.skills_depth) / 2.0
    experience = (
        dimension_scores.title_trajectory
        + dimension_scores.seniority_alignment
        + dimension_scores.industry_match
        + dimension_scores.tenure_pattern
    ) / 4.0
    return round(skills, 2), round(experience, 2)


def compute_role_fit(cv_fit: float, requirements_match: float) -> float:
    """role_fit = 0.40 × cv_fit + 0.60 × requirements_match."""
    return round(0.40 * cv_fit + 0.60 * requirements_match, 2)


def derive_recommendation(
    role_fit: float,
    *,
    has_failed_constraint: bool,
    has_missing_must_have: bool,
) -> Recommendation:
    """Hard rules first (constraint failures), then score thresholds."""
    if has_failed_constraint:
        return Recommendation.NO

    if has_missing_must_have:
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
    dimension_scores: DimensionScores | None,
    assessments: Iterable[RequirementAssessment],
    archetype_weights: dict[str, float] | None = None,
) -> tuple[float, float, float, float, float, Recommendation]:
    """Run the full aggregation chain.

    Returns (skills_match, experience_relevance, requirements_match,
    cv_fit, role_fit, recommendation).

    skills_match + experience_relevance are derived from the dimensions
    for legacy-consumer compatibility.
    """
    assessments_list = list(assessments)
    requirements_match = compute_requirements_match_score(assessments_list)
    cv_fit = compute_cv_fit_from_dimensions(dimension_scores, archetype_weights)
    role_fit = compute_role_fit(cv_fit, requirements_match)
    skills_match, experience_relevance = derive_v3_compat_scores(dimension_scores)

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
    return (
        skills_match,
        experience_relevance,
        requirements_match,
        cv_fit,
        role_fit,
        recommendation,
    )
