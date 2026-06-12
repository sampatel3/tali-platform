"""Deterministic aggregation over LLM-emitted CV match data.

The LLM produces six dimension scores + per-requirement assessments.
Everything else is derived here.
"""

from __future__ import annotations

import os
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
    Priority.STRONG_PREFERENCE: 0.40,
    Priority.NICE_TO_HAVE: 0.15,
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

# Recruiter-added requirements (id NOT prefixed with "jd_req_") get a
# bumped priority weight: the recruiter knows what they're hiring for,
# so their explicit asks carry more signal than what the LLM extracted
# from the JD prose. 1.5x means a recruiter-added must_have effectively
# weighs as much as ~1.5 LLM-extracted must_haves.
_RECRUITER_WEIGHT_MULTIPLIER = 1.5

# --- requirements_match v2 (coverage-blended, must-have-normalised) ---------
# Fixed must-have share of the assessed score, INDEPENDENT of how many
# must-haves vs preferences a role has — so a single must-have can't be
# drowned by a long tail of preferences. Redistributes to preferences when a
# role has no must-haves (and vice-versa).
_MUST_SHARE = 0.65
# A candidate whose requirements are mostly UNKNOWN (unassessable from the CV)
# is blended toward this neutral prior in proportion to how little we could
# assess — so "unknown" never penalises (it's not 0), but a candidate we can
# barely assess also can't score high off one met requirement.
_REQ_MATCH_NEUTRAL_PRIOR = 50.0


def _req_match_v2_enabled() -> bool:
    """Env-gated rollout. Read per-call so a deploy toggle takes effect
    without a code change; the harness calls the v1/v2 helpers directly."""
    return os.getenv("CV_MATCH_REQ_MATCH_V2", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


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


def _recruiter_weight_multiplier(assessment) -> float:
    """Recruiter-added requirements (id NOT prefixed ``jd_req_``) get a
    1.5x priority weight. The recruiter knows what they're hiring for;
    their explicit asks carry more signal than what the LLM extracted
    from the JD prose."""
    rid = (getattr(assessment, "requirement_id", "") or "").lower()
    if rid.startswith("jd_req_"):
        return 1.0
    return _RECRUITER_WEIGHT_MULTIPLIER


def compute_requirements_match_score(
    assessments: Iterable[RequirementAssessment],
) -> float:
    """Requirements-match score (0–100).

    Dispatch order:
    1. **graded** — when any assessment carries a graded ``match_score`` (>=0,
       set by the focused ``cv_matching.graded`` pass). The continuous 0-100
       per-requirement score replaces the coarse ``status × tier`` weighting,
       fixing the double-penalty (strong substitute scoring below a vague
       partial) and the discarded-evidence problem.
    2. **v2** — coverage-blended, must-have-normalised (``CV_MATCH_REQ_MATCH_V2``).
    3. **v1** — original flat weighted average.
    """
    assessments_list = list(assessments)
    if _has_graded(assessments_list):
        return compute_requirements_match_score_graded(assessments_list)
    if _req_match_v2_enabled():
        return compute_requirements_match_score_v2(assessments_list)
    return compute_requirements_match_score_v1(assessments_list)


def compute_requirements_match_score_v1(
    assessments: Iterable[RequirementAssessment],
) -> float:
    """Original flat weighted average across requirements.

    Per-requirement weight = priority × status × tier × recruiter_bump.
    Known weakness: every requirement (including unassessable "unknown" ones)
    keeps its full priority weight in the denominator, so a long tail of
    strong-preference items — and the scorer's own uncertainty — drag down a
    candidate who meets the actual must-haves. v2 fixes this.
    """
    total_weight = 0.0
    earned_weight = 0.0
    for a in assessments:
        if a.priority == Priority.CONSTRAINT:
            continue
        priority_weight = _PRIORITY_WEIGHTS.get(a.priority, 0.0)
        status_weight = _STATUS_WEIGHTS.get(a.status, 0.0)
        tier_multiplier = _tier_multiplier(a)
        recruiter_bump = _recruiter_weight_multiplier(a)
        effective_weight = priority_weight * recruiter_bump
        total_weight += effective_weight
        earned_weight += effective_weight * status_weight * tier_multiplier

    if total_weight <= 0:
        return 50.0

    return round((earned_weight / total_weight) * 100.0, 2)


def _fulfilment(a: RequirementAssessment) -> float:
    """Graded 0..1 met-ness for an ASSESSED requirement (status × tier).
    `met·exact`=1.0, `partially_met`≈0.5, `missing`/`unrelated`=0.0 — so a
    candidate *close* to a requirement earns partial credit rather than a
    binary pass/fail."""
    return _STATUS_WEIGHTS.get(a.status, 0.0) * _tier_multiplier(a)


def compute_requirements_match_score_v2(
    assessments: Iterable[RequirementAssessment],
) -> float:
    """Coverage-blended, must-have-normalised requirements match.

    Three changes from v1, targeting the calibration gap where candidates who
    meet every must-have still score low:

    1. **Two tiers, count-normalised.** Must-haves and preferences are scored
       separately, then combined with a FIXED must-have share (``_MUST_SHARE``)
       regardless of how many of each the role has — so 1 must-have can't be
       drowned by 50 preferences. (Redistributes when a tier is absent.)
    2. **Must-have as a graded soft-constraint.** Each requirement contributes
       a 0..1 fulfilment; a candidate close to a must-have earns partial credit,
       a candidate missing it is dragged down hard (0 × 0.65 share) but not
       hard-gated.
    3. **Unknown is neutral, not a penalty — with a coverage guard.** Unknown
       (unassessable) requirements are excluded from the tier averages, then the
       whole score is blended toward a neutral 50 in proportion to how little of
       the requirement set could be assessed. So unknowns never count as 0, but
       a candidate we can barely assess (e.g. 1 must-have met, everything else
       unknown) lands near 50 rather than scoring high.
    """
    must_num = must_den = 0.0
    pref_num = pref_den = 0.0
    assessed_weight = total_weight = 0.0
    for a in assessments:
        if a.priority == Priority.CONSTRAINT:
            continue
        weight = _PRIORITY_WEIGHTS.get(a.priority, 0.0) * _recruiter_weight_multiplier(a)
        if weight <= 0:
            continue
        total_weight += weight
        if a.status == Status.UNKNOWN:
            continue  # unassessable → affects coverage only, never penalised
        assessed_weight += weight
        earned = weight * _fulfilment(a)
        if a.priority == Priority.MUST_HAVE:
            must_num += earned
            must_den += weight
        else:
            pref_num += earned
            pref_den += weight

    if total_weight <= 0:
        return 50.0

    must_f = (must_num / must_den) if must_den > 0 else None
    pref_f = (pref_num / pref_den) if pref_den > 0 else None
    if must_f is None and pref_f is None:
        assessed_score = _REQ_MATCH_NEUTRAL_PRIOR / 100.0  # nothing assessed
    elif must_f is None:
        assessed_score = pref_f
    elif pref_f is None:
        assessed_score = must_f
    else:
        assessed_score = _MUST_SHARE * must_f + (1.0 - _MUST_SHARE) * pref_f

    coverage = assessed_weight / total_weight  # 0..1
    blended = coverage * (assessed_score * 100.0) + (1.0 - coverage) * _REQ_MATCH_NEUTRAL_PRIOR
    return round(blended, 2)


def _has_graded(assessments: Iterable[RequirementAssessment]) -> bool:
    """True when any assessment carries a graded ``match_score`` (>= 0).

    The graded pass (``cv_matching.graded``) sets ``match_score`` to 0-100;
    legacy/ungraded assessments keep the ``-1`` sentinel.
    """
    return any(getattr(a, "match_score", -1) >= 0 for a in assessments)


def compute_requirements_match_score_graded(
    assessments: Iterable[RequirementAssessment],
) -> float:
    """Graded requirements match — same coverage-blended, must-have-normalised
    shape as v2, but each requirement contributes its continuous 0-100
    ``match_score`` (÷100) instead of ``status_weight × tier_weight``.

    This removes the double-penalty (a strong equivalent skill no longer
    scores below a vague partial — it is graded ~0.75-0.85 directly) and stops
    discarding evidence the coarse model abstained on. ``assessable=False`` (or
    a missing/sentinel ``match_score``) is treated like ``unknown``: excluded
    from the tier averages, affecting coverage only — never a 0 penalty.
    """
    must_num = must_den = 0.0
    pref_num = pref_den = 0.0
    assessed_weight = total_weight = 0.0
    for a in assessments:
        if a.priority == Priority.CONSTRAINT:
            continue
        weight = _PRIORITY_WEIGHTS.get(a.priority, 0.0) * _recruiter_weight_multiplier(a)
        if weight <= 0:
            continue
        total_weight += weight
        ms = getattr(a, "match_score", -1)
        if not getattr(a, "assessable", True) or ms < 0:
            continue  # unassessable → coverage only, never penalised
        assessed_weight += weight
        fulfilment = max(0.0, min(100.0, float(ms))) / 100.0
        if a.priority == Priority.MUST_HAVE:
            must_num += weight * fulfilment
            must_den += weight
        else:
            pref_num += weight * fulfilment
            pref_den += weight

    if total_weight <= 0:
        return 50.0

    must_f = (must_num / must_den) if must_den > 0 else None
    pref_f = (pref_num / pref_den) if pref_den > 0 else None
    if must_f is None and pref_f is None:
        assessed_score = _REQ_MATCH_NEUTRAL_PRIOR / 100.0
    elif must_f is None:
        assessed_score = pref_f
    elif pref_f is None:
        assessed_score = must_f
    else:
        assessed_score = _MUST_SHARE * must_f + (1.0 - _MUST_SHARE) * pref_f

    coverage = assessed_weight / total_weight
    blended = coverage * (assessed_score * 100.0) + (1.0 - coverage) * _REQ_MATCH_NEUTRAL_PRIOR
    return round(blended, 2)


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
    """Pure score-threshold recommendation.

    Constraint failures and missing must-haves no longer hard-cap the
    recommendation — the underlying weighted aggregation already
    discounts those candidates via priority × status × tier weights.
    The boolean flags are accepted on the signature for backwards
    compatibility but ignored.
    """
    del has_failed_constraint, has_missing_must_have  # no longer used
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
) -> tuple[float, float, float, float, float]:
    """Run the full aggregation chain.

    Returns (skills_match, experience_relevance, requirements_match,
    cv_fit, role_fit).

    No recommendation is returned — the recruiter sets a per-role
    reject threshold on the job page; the UI derives the indicator
    dynamically from ``role_fit_score`` against that threshold.

    skills_match + experience_relevance are derived from the
    dimensions for legacy-consumer compatibility.
    """
    assessments_list = list(assessments)
    requirements_match = compute_requirements_match_score(assessments_list)
    cv_fit = compute_cv_fit_from_dimensions(dimension_scores, archetype_weights)
    role_fit = compute_role_fit(cv_fit, requirements_match)
    skills_match, experience_relevance = derive_v3_compat_scores(dimension_scores)
    return (
        skills_match,
        experience_relevance,
        requirements_match,
        cv_fit,
        role_fit,
    )
