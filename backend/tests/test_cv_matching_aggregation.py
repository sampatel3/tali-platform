"""Tests for backend/app/cv_matching/aggregation.py.

Covers:
- The worked example from calibration.md
- All priority × status combinations
- Both floor caps (constraint, must_have)
- Edge cases (no requirements, all-constraint, total_weight=0)
- Recommendation thresholds and the missing-must-have cap rule
"""

from __future__ import annotations

import pytest

from app.cv_matching.aggregation import (
    aggregate,
    compute_cv_fit,
    compute_requirements_match_score,
    compute_role_fit,
    derive_recommendation,
)
from app.cv_matching.schemas import (
    Confidence,
    Priority,
    Recommendation,
    RequirementAssessment,
    Status,
)


def _ra(
    rid: str,
    priority: Priority,
    status: Status,
) -> RequirementAssessment:
    return RequirementAssessment(
        requirement_id=rid,
        requirement=rid,
        priority=priority,
        status=status,
        evidence_quote="" if status in (Status.MISSING, Status.UNKNOWN) else "x",
        evidence_start_char=-1 if status in (Status.MISSING, Status.UNKNOWN) else 0,
        evidence_end_char=-1 if status in (Status.MISSING, Status.UNKNOWN) else 1,
        impact="",
        confidence=Confidence.MEDIUM,
    )


# ---------- compute_cv_fit ----------


def test_compute_cv_fit_simple_average():
    assert compute_cv_fit(78, 72) == 75.0
    assert compute_cv_fit(100, 0) == 50.0
    assert compute_cv_fit(0, 0) == 0.0


# ---------- compute_role_fit ----------


def test_compute_role_fit_weighting():
    # 0.4 * 75 + 0.6 * 61.76 = 30 + 37.056 = 67.056 → 67.06
    assert compute_role_fit(75.0, 61.76) == 67.06


def test_compute_role_fit_extremes():
    assert compute_role_fit(100, 100) == 100.0
    assert compute_role_fit(0, 0) == 0.0


# ---------- compute_requirements_match_score ----------


def test_worked_example_from_calibration_md():
    """Reproduces the worked example exactly: 5 reqs, expected 61.76."""
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
    """No non-constraint reqs → total_weight=0 → neutral 50."""
    assessments = [
        _ra("c1", Priority.CONSTRAINT, Status.MET),
        _ra("c2", Priority.CONSTRAINT, Status.MET),
    ]
    assert compute_requirements_match_score(assessments) == 50.0


def test_all_must_haves_met():
    assessments = [
        _ra("a", Priority.MUST_HAVE, Status.MET),
        _ra("b", Priority.MUST_HAVE, Status.MET),
    ]
    assert compute_requirements_match_score(assessments) == 100.0


def test_all_status_combinations_for_must_have():
    cases = [
        (Status.MET, 100.0),
        (Status.PARTIALLY_MET, 50.0),
        (Status.UNKNOWN, 30.0),
        (Status.MISSING, 0.0),
    ]
    for status, expected in cases:
        assessments = [_ra("a", Priority.MUST_HAVE, status)]
        if status in (Status.MISSING, Status.UNKNOWN):
            # MUST_HAVE missing → must-have floor caps at 40
            score = compute_requirements_match_score(assessments)
            assert score == min(expected, 40.0), (status, score)
        else:
            assert compute_requirements_match_score(assessments) == expected


def test_constraint_disqualifying_floor():
    """Disqualifying constraint missing → score capped at 30."""
    assessments = [
        _ra("a", Priority.MUST_HAVE, Status.MET),  # would yield 100 alone
    ]
    assessments.append(
        RequirementAssessment(
            requirement_id="loc",
            requirement="UAE",
            priority=Priority.CONSTRAINT,
            status=Status.MISSING,
            evidence_quote="",
            evidence_start_char=-1,
            evidence_end_char=-1,
            impact="",
            confidence=Confidence.MEDIUM,
        )
    )
    # MUST_HAVE met yields 100 base; constraint floor caps to 30.
    assert compute_requirements_match_score(assessments) == 30.0


def test_must_have_disqualifying_floor():
    """Missing must_have caps the score at 40."""
    assessments = [
        _ra("ok", Priority.STRONG_PREFERENCE, Status.MET),
        _ra("missed", Priority.MUST_HAVE, Status.MISSING),
    ]
    score = compute_requirements_match_score(assessments)
    assert score <= 40.0


def test_both_floors_apply_minimum():
    """When both floors trigger, the lower (constraint=30) wins."""
    assessments = [
        _ra("missed_mh", Priority.MUST_HAVE, Status.MISSING),
        _ra("missed_c", Priority.CONSTRAINT, Status.MISSING),
    ]
    score = compute_requirements_match_score(assessments)
    assert score <= 30.0


def test_unknown_must_have_triggers_floor():
    """Unknown is treated as unfulfilled for floor purposes."""
    assessments = [
        _ra("ok", Priority.STRONG_PREFERENCE, Status.MET),
        _ra("unk", Priority.MUST_HAVE, Status.UNKNOWN),
    ]
    score = compute_requirements_match_score(assessments)
    assert score <= 40.0


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
            role_fit,
            has_failed_constraint=False,
            has_missing_must_have=False,
        )
        assert got == expected, (role_fit, got, expected)


def test_failed_constraint_forces_no():
    for role_fit in (0.0, 50.0, 85.0, 100.0):
        assert (
            derive_recommendation(
                role_fit, has_failed_constraint=True, has_missing_must_have=False
            )
            == Recommendation.NO
        )


def test_missing_must_have_caps_at_lean_no():
    """Missing must_have means recommendation is at most LEAN_NO."""
    # Even with role_fit high enough for STRONG_YES, capped at LEAN_NO.
    assert (
        derive_recommendation(
            95.0, has_failed_constraint=False, has_missing_must_have=True
        )
        == Recommendation.LEAN_NO
    )
    # Below 50 still falls through to NO (LEAN_NO cap doesn't override NO).
    assert (
        derive_recommendation(
            30.0, has_failed_constraint=False, has_missing_must_have=True
        )
        == Recommendation.NO
    )


def test_constraint_failure_outranks_must_have_cap():
    assert (
        derive_recommendation(
            95.0, has_failed_constraint=True, has_missing_must_have=True
        )
        == Recommendation.NO
    )


# ---------- aggregate (full chain) ----------


def test_aggregate_worked_example():
    """End-to-end on the calibration.md example."""
    assessments = [
        _ra("req_1", Priority.MUST_HAVE, Status.MET),
        _ra("req_2", Priority.MUST_HAVE, Status.PARTIALLY_MET),
        _ra("req_3", Priority.STRONG_PREFERENCE, Status.MISSING),
        _ra("req_4", Priority.CONSTRAINT, Status.MET),
        _ra("req_5", Priority.NICE_TO_HAVE, Status.MISSING),
    ]
    req_match, cv_fit, role_fit, rec = aggregate(
        skills_match_score=78.0,
        experience_relevance_score=72.0,
        assessments=assessments,
    )
    assert req_match == 61.76
    assert cv_fit == 75.0
    assert role_fit == 67.06
    assert rec == Recommendation.LEAN_NO


def test_aggregate_strong_yes_path():
    assessments = [
        _ra("a", Priority.MUST_HAVE, Status.MET),
        _ra("b", Priority.MUST_HAVE, Status.MET),
        _ra("c", Priority.STRONG_PREFERENCE, Status.MET),
    ]
    _, _, role_fit, rec = aggregate(
        skills_match_score=92.0,
        experience_relevance_score=90.0,
        assessments=assessments,
    )
    assert role_fit >= 85.0
    assert rec == Recommendation.STRONG_YES


def test_aggregate_constraint_failure_short_circuits_to_no():
    assessments = [
        _ra("ok", Priority.MUST_HAVE, Status.MET),
        _ra("loc", Priority.CONSTRAINT, Status.MISSING),
    ]
    _, _, _, rec = aggregate(
        skills_match_score=90.0,
        experience_relevance_score=90.0,
        assessments=assessments,
    )
    assert rec == Recommendation.NO
