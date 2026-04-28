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

# Match-tier multipliers (v4 only). Applied on top of priority × status.
# v3 assessments have no match_tier attribute, so the helper below
# returns 1.0 for them — v3 aggregation results are byte-identical to
# the pre-v4 implementation.
_TIER_WEIGHTS: dict[str, float] = {
    "exact": 1.0,
    "strong_substitute": 0.85,
    "weak_substitute": 0.55,
    "unrelated": 0.0,
    "missing": 0.0,
}


def _tier_multiplier(assessment) -> float:
    tier = getattr(assessment, "match_tier", None)
    if tier is None:
        return 1.0  # v3 path — no tier, full credit
    return _TIER_WEIGHTS.get(tier, 1.0)

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
        tier_multiplier = _tier_multiplier(a)
        total_weight += priority_weight
        earned_weight += priority_weight * status_weight * tier_multiplier

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
    """Simple average of the two LLM-produced sub-scores (v3 / v4.1)."""
    return round((skills_match_score + experience_relevance_score) / 2.0, 2)


_DEFAULT_DIMENSION_WEIGHTS = {
    "skills_coverage": 0.25,
    "skills_depth": 0.20,
    "title_trajectory": 0.15,
    "seniority_alignment": 0.15,
    "industry_match": 0.15,
    "tenure_pattern": 0.10,
}


def compute_cv_fit_v4_2(
    dimension_scores,
    weights: dict[str, float] | None = None,
) -> float:
    """v4.2 cv_fit derived from six dimension scores.

    ``weights`` is a per-archetype dict from the rubric YAML. When
    None, the default weighting is used. Missing keys default to the
    six-dimension default; weights are renormalised to sum to 1.0
    before applying.
    """
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


def derive_v3_compat_scores(dimension_scores) -> tuple[float, float]:
    """Project six v4.2 dimensions back onto the v3 (skills, experience) pair.

    skills_match_score          = mean(skills_coverage, skills_depth)
    experience_relevance_score  = mean(title_trajectory, seniority_alignment,
                                       industry_match, tenure_pattern)

    These are the legacy fields some callers still read; populating
    them from the v4.2 dimensions keeps consumers like
    ``role_support.py`` working without conditional code.
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
