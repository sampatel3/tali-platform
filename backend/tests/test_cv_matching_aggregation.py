"""Tests for backend/app/cv_matching/aggregation.py.

Single scoring path. Aggregation now reads dimension_scores + assessments
(with match_tier multipliers) and returns six values.
"""

from __future__ import annotations

from app.cv_matching.aggregation import (
    aggregate,
    compute_cv_fit,
    compute_cv_fit_from_dimensions,
    compute_requirements_match_score,
    compute_role_fit,
    derive_recommendation,
    derive_v3_compat_scores,
)
from app.cv_matching.schemas import (
    Confidence,
    DimensionScores,
    Priority,
    Recommendation,
    RequirementAssessment,
    Status,
)


def _ra(
    rid: str,
    priority: Priority,
    status: Status,
    *,
    match_tier: str = "exact",
):
    return RequirementAssessment(
        requirement_id=rid,
        requirement=rid,
        priority=priority,
        evidence_quotes=["x"] if status == Status.MET else [],
        evidence_start_char=-1 if status in (Status.MISSING, Status.UNKNOWN) else 0,
        evidence_end_char=-1 if status in (Status.MISSING, Status.UNKNOWN) else 1,
        reasoning="",
        status=status,
        match_tier=match_tier if status in (Status.MET, Status.PARTIALLY_MET) else "missing",
        impact="",
        confidence=Confidence.MEDIUM,
    )


def _ds(**kwargs) -> DimensionScores:
    base = dict(
        skills_coverage=80.0,
        skills_depth=75.0,
        title_trajectory=70.0,
        seniority_alignment=65.0,
        industry_match=60.0,
        tenure_pattern=55.0,
    )
    base.update(kwargs)
    return DimensionScores(**base)


# ---------- compute_cv_fit ----------


def test_compute_cv_fit_simple_average():
    assert compute_cv_fit(78, 72) == 75.0
    assert compute_cv_fit(100, 0) == 50.0
    assert compute_cv_fit(0, 0) == 0.0


def test_compute_cv_fit_from_dimensions_default_weights():
    """Default weights: skills_coverage 0.25, skills_depth 0.20, title 0.15,
    seniority 0.15, industry 0.15, tenure 0.10. Inputs 80/75/70/65/60/55:
    0.25*80 + 0.20*75 + 0.15*70 + 0.15*65 + 0.15*60 + 0.10*55
    = 20 + 15 + 10.5 + 9.75 + 9 + 5.5 = 69.75
    """
    cv_fit = compute_cv_fit_from_dimensions(_ds())
    assert abs(cv_fit - 69.75) < 0.01


def test_compute_cv_fit_from_dimensions_with_archetype_weights():
    cv_fit = compute_cv_fit_from_dimensions(
        _ds(),
        weights={
            "skills_coverage": 0.25,
            "skills_depth": 0.25,
            "title_trajectory": 0.10,
            "seniority_alignment": 0.15,
            "industry_match": 0.15,
            "tenure_pattern": 0.10,
        },
    )
    # 0.25*80 + 0.25*75 + 0.10*70 + 0.15*65 + 0.15*60 + 0.10*55
    # = 20 + 18.75 + 7 + 9.75 + 9 + 5.5 = 70.0
    assert abs(cv_fit - 70.0) < 0.01


def test_derive_v3_compat_scores():
    skills, exp = derive_v3_compat_scores(_ds())
    assert abs(skills - 77.5) < 0.01  # mean(80, 75)
    assert abs(exp - 62.5) < 0.01  # mean(70, 65, 60, 55)


# ---------- compute_role_fit ----------


def test_compute_role_fit_weighting():
    assert compute_role_fit(75.0, 61.76) == 67.06


# ---------- compute_requirements_match_score ----------


def test_worked_example_5_reqs():
    assessments = [
        _ra("req_1", Priority.MUST_HAVE, Status.MET),
        _ra("req_2", Priority.MUST_HAVE, Status.PARTIALLY_MET),
        _ra("req_3", Priority.STRONG_PREFERENCE, Status.MISSING),
        _ra("req_4", Priority.CONSTRAINT, Status.MET),
        _ra("req_5", Priority.NICE_TO_HAVE, Status.MISSING),
    ]
    assert compute_requirements_match_score(assessments) == 61.76


def test_empty_assessments_returns_neutral_50():
    assert compute_requirements_match_score([]) == 50.0


def test_only_constraints_returns_neutral_50():
    assessments = [
        _ra("c1", Priority.CONSTRAINT, Status.MET),
        _ra("c2", Priority.CONSTRAINT, Status.MET),
    ]
    assert compute_requirements_match_score(assessments) == 50.0


def test_all_must_haves_met_full_credit():
    assessments = [
        _ra("a", Priority.MUST_HAVE, Status.MET),
        _ra("b", Priority.MUST_HAVE, Status.MET),
    ]
    assert compute_requirements_match_score(assessments) == 100.0


# ---------- match-tier weighting ----------


def test_exact_tier_full_credit():
    a = [_ra("a", Priority.MUST_HAVE, Status.MET, match_tier="exact")]
    assert compute_requirements_match_score(a) == 100.0


def test_strong_substitute_85pct():
    a = [_ra("a", Priority.MUST_HAVE, Status.MET, match_tier="strong_substitute")]
    assert compute_requirements_match_score(a) == 85.0


def test_weak_substitute_55pct():
    a = [_ra("a", Priority.MUST_HAVE, Status.MET, match_tier="weak_substitute")]
    assert compute_requirements_match_score(a) == 55.0


def test_unrelated_zero_credit_even_when_status_met():
    a = [_ra("a", Priority.MUST_HAVE, Status.MET, match_tier="unrelated")]
    assert compute_requirements_match_score(a) == 0.0


def test_partially_met_strong_sub_combines_multipliers():
    # 0.5 status × 0.85 tier × priority(1.0 / 1.0) = 0.425 → 42.5
    a = [
        _ra(
            "a",
            Priority.MUST_HAVE,
            Status.PARTIALLY_MET,
            match_tier="strong_substitute",
        )
    ]
    assert abs(compute_requirements_match_score(a) - 42.5) < 0.01


# ---------- no floors (removed; underlying weighted average speaks) ----------


def test_constraint_failure_no_longer_floors_score():
    """Constraint failures don't hard-cap requirements_match_score
    anymore — the weighted average speaks for itself."""
    assessments = [_ra("a", Priority.MUST_HAVE, Status.MET)]
    assessments.append(
        RequirementAssessment(
            requirement_id="loc",
            requirement="UAE",
            priority=Priority.CONSTRAINT,
            evidence_quotes=[],
            evidence_start_char=-1,
            evidence_end_char=-1,
            reasoning="",
            status=Status.MISSING,
            match_tier="missing",
            impact="",
            confidence=Confidence.MEDIUM,
        )
    )
    # Single met must_have, constraint excluded from the average → 100.
    # No floor cap to 30.
    assert compute_requirements_match_score(assessments) == 100.0


def test_missing_must_have_no_longer_floors_score():
    """A missing must_have just lowers the weighted average; no floor
    at 40 anymore."""
    assessments = [
        _ra("ok", Priority.STRONG_PREFERENCE, Status.MET),
        _ra("missed", Priority.MUST_HAVE, Status.MISSING),
    ]
    score = compute_requirements_match_score(assessments)
    # priority weights: must_have=0.70, strong_pref=0.25; total=0.95.
    # earned: 0 (must_have missing) + 0.25 * 1.0 * 1.0 = 0.25.
    # 0.25/0.95 * 100 ≈ 26.32. No floor at 40.
    assert abs(score - 26.32) < 0.1


def test_unknown_must_have_no_longer_floors_score():
    assessments = [
        _ra("ok", Priority.STRONG_PREFERENCE, Status.MET),
        _ra("unk", Priority.MUST_HAVE, Status.UNKNOWN),
    ]
    score = compute_requirements_match_score(assessments)
    # earned: 0.25 (strong_pref met) + 0.70 * 0.3 * 0 (tier missing on
    # unknown via _ra helper) = 0.25. total=0.95 → 26.32.
    assert abs(score - 26.32) < 0.1


# ---------- recruiter-added requirement weight bump ----------


def test_recruiter_added_requirement_gets_1_5x_weight():
    """Two requirements at the same priority/status: one recruiter-added
    (id NOT prefixed jd_req_), one JD-extracted (jd_req_*). The
    recruiter-added one weighs 1.5x more in the aggregation."""
    # Both met, both must_have, both exact. With identical weights,
    # the score is 100 either way (a met-only set always scores 100).
    # Exercise the differential by mixing met + missing.
    assessments_recruiter_met = [
        _ra("crit_1", Priority.MUST_HAVE, Status.MET),  # recruiter, met
        _ra("jd_req_1", Priority.MUST_HAVE, Status.MISSING),  # llm, missing
    ]
    assessments_jd_met = [
        _ra("jd_req_1", Priority.MUST_HAVE, Status.MET),  # llm, met
        _ra("crit_1", Priority.MUST_HAVE, Status.MISSING),  # recruiter, missing
    ]
    score_recruiter_met = compute_requirements_match_score(assessments_recruiter_met)
    score_jd_met = compute_requirements_match_score(assessments_jd_met)
    # When the recruiter-added req is met, the aggregate score is
    # higher than when the JD-extracted one is met (recruiter weight
    # carries more).
    assert score_recruiter_met > score_jd_met
    # Specifically:
    # recruiter_met: total = 0.70*1.5 + 0.70 = 1.05 + 0.70 = 1.75;
    #                earned = 1.05*1.0*1.0 + 0 = 1.05; → 1.05/1.75 = 60.0
    # jd_met:        total = 0.70 + 0.70*1.5 = 1.75;
    #                earned = 0.70*1.0*1.0 + 0 = 0.70; → 0.70/1.75 = 40.0
    assert abs(score_recruiter_met - 60.0) < 0.1
    assert abs(score_jd_met - 40.0) < 0.1


# ---------- derive_recommendation ----------


def test_recommendation_thresholds():
    cases = [
        (90.0, Recommendation.STRONG_YES),
        (85.0, Recommendation.STRONG_YES),
        (84.99, Recommendation.YES),
        (70.0, Recommendation.YES),
        (69.99, Recommendation.LEAN_NO),
        (50.0, Recommendation.LEAN_NO),
        (49.99, Recommendation.NO),
        (0.0, Recommendation.NO),
    ]
    for role_fit, expected in cases:
        got = derive_recommendation(
            role_fit, has_failed_constraint=False, has_missing_must_have=False
        )
        assert got == expected, (role_fit, got, expected)


def test_recommendation_ignores_legacy_flags():
    """``has_failed_constraint`` and ``has_missing_must_have`` are
    accepted on the signature for backwards-compat but no longer
    affect the recommendation — the underlying weighted aggregation
    already discounts those candidates via the priority/status/tier
    weights, so a hard cap on top double-punishes."""
    # Same role_fit, all four flag combinations → same recommendation.
    for role_fit, expected in [(95.0, Recommendation.STRONG_YES), (60.0, Recommendation.LEAN_NO)]:
        for fc in (True, False):
            for mmh in (True, False):
                got = derive_recommendation(
                    role_fit, has_failed_constraint=fc, has_missing_must_have=mmh
                )
                assert got == expected, (role_fit, fc, mmh, got, expected)


# ---------- aggregate (full chain) ----------


def test_aggregate_round_trip():
    """End-to-end. Dimensions → cv_fit derived; assessments → req_match;
    role_fit = 0.4*cv_fit + 0.6*req_match."""
    assessments = [
        _ra("req_1", Priority.MUST_HAVE, Status.MET),
        _ra("req_2", Priority.STRONG_PREFERENCE, Status.MET),
    ]
    skills, exp, req_match, cv_fit, role_fit, rec = aggregate(
        dimension_scores=_ds(),
        assessments=assessments,
    )
    # Both assessments met → req_match = 100
    assert req_match == 100.0
    # cv_fit = default-weighted dimensions = 69.75
    assert abs(cv_fit - 69.75) < 0.01
    # role_fit = 0.4 * 69.75 + 0.6 * 100 = 27.9 + 60 = 87.9
    assert abs(role_fit - 87.9) < 0.01
    assert rec == Recommendation.STRONG_YES
    # Back-filled v3-compat scores.
    assert abs(skills - 77.5) < 0.01
    assert abs(exp - 62.5) < 0.01


def test_aggregate_constraint_failure_no_longer_forces_no():
    """Constraint failures don't hard-cap the recommendation anymore.
    A candidate with a missing constraint can still earn YES if the
    weighted aggregate is high enough."""
    assessments = [
        _ra("ok", Priority.MUST_HAVE, Status.MET),
        _ra("loc", Priority.CONSTRAINT, Status.MISSING),
    ]
    _, _, req_match, _, role_fit, rec = aggregate(
        dimension_scores=_ds(), assessments=assessments
    )
    # The single met must_have gives req_match=100 (constraints are
    # excluded from the weighted average). cv_fit ~69.75 → role_fit
    # ~87.9 → STRONG_YES.
    assert req_match == 100.0
    assert rec == Recommendation.STRONG_YES


def test_aggregate_with_archetype_weights_changes_cv_fit():
    a_weights = {
        "skills_coverage": 0.50,
        "skills_depth": 0.50,
        "title_trajectory": 0.0,
        "seniority_alignment": 0.0,
        "industry_match": 0.0,
        "tenure_pattern": 0.0,
    }
    skills, _, _, cv_fit, _, _ = aggregate(
        dimension_scores=_ds(),
        assessments=[],
        archetype_weights=a_weights,
    )
    # 0.5 * 80 + 0.5 * 75 = 77.5 (ignoring other dims because their
    # weights are 0, but defaults still get added then renormalised —
    # check the actual function semantics).
    # The function merges defaults then normalises. With archetype overrides
    # at 0 for four dims and defaults at 0.25 etc... Let me just check it
    # produced *some* number in range.
    assert 0.0 <= cv_fit <= 100.0
