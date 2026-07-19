"""Graded per-requirement scoring (cv_match_v18).

The aggregation switches from binary ``status × tier`` to a continuous
0-100 ``match_score`` whenever the graded pass has populated it. These
tests lock the properties that motivated the change.
"""

from app.cv_matching.schemas import RequirementAssessment, Priority, Status
from app.cv_matching.aggregation import (
    compute_requirements_match_score,
    _has_graded,
)


def _ra(rid, priority, match_score, assessable=True, status=Status.MET):
    return RequirementAssessment(
        requirement_id=rid,
        requirement=f"requirement {rid}",
        priority=priority,
        status=status,
        match_tier="exact",
        match_score=match_score,
        assessable=assessable,
        evidence_quotes=["evidence"] if match_score >= 40 else [],
    )


def test_has_graded_dispatch():
    graded = [_ra("a", Priority.MUST_HAVE, 80)]
    legacy = [_ra("a", Priority.MUST_HAVE, -1)]
    assert _has_graded(graded) is True
    assert _has_graded(legacy) is False


def test_graded_path_used_when_match_score_present():
    # A single must-have at match_score=80 should land near 80, NOT be
    # collapsed to a binary bucket.
    score = compute_requirements_match_score([_ra("a", Priority.MUST_HAVE, 80)])
    assert 70 <= score <= 90


def test_strong_substitute_no_longer_double_penalised():
    # The bug v18 fixes: under binary, partially_met × strong_substitute
    # (0.5×0.85=0.425) scored BELOW partially_met × exact (0.5). Graded scores
    # a strong equivalent ~85 directly, so a strong-substitute candidate beats
    # a genuinely-vague one.
    strong = compute_requirements_match_score([_ra("a", Priority.MUST_HAVE, 85)])
    vague = compute_requirements_match_score([_ra("a", Priority.MUST_HAVE, 50)])
    assert strong > vague
    assert strong >= 80


def test_assessable_false_is_neutral_not_zero():
    # 1 must-have fully met + several "no evidence either way" preferences.
    # The abstained prefs must NOT drag the score down (excluded from the
    # average, coverage only) — unlike a 0 match_score.
    abstained = [_ra("m", Priority.MUST_HAVE, 90)] + [
        _ra(f"p{i}", Priority.STRONG_PREFERENCE, 0, assessable=False, status=Status.UNKNOWN)
        for i in range(5)
    ]
    zeroed = [_ra("m", Priority.MUST_HAVE, 90)] + [
        _ra(f"p{i}", Priority.STRONG_PREFERENCE, 0, status=Status.MISSING) for i in range(5)
    ]
    assert compute_requirements_match_score(abstained) > compute_requirements_match_score(zeroed)


def test_must_have_not_drowned_by_many_preferences():
    # 1 strong must-have + 50 weak preferences. The fixed 0.65 must-share keeps
    # the must-have meaningful instead of being averaged into the noise.
    reqs = [_ra("m", Priority.MUST_HAVE, 90)] + [
        _ra(f"p{i}", Priority.STRONG_PREFERENCE, 20) for i in range(50)
    ]
    score = compute_requirements_match_score(reqs)
    # 0.65*0.9 + 0.35*0.2 = 0.655 → ~65, NOT ~22 (drowned).
    assert 55 <= score <= 72


def test_separation_low_vs_high():
    low = compute_requirements_match_score([
        _ra("m", Priority.MUST_HAVE, 5, status=Status.MISSING),
        _ra("p", Priority.STRONG_PREFERENCE, 10, status=Status.MISSING),
    ])
    high = compute_requirements_match_score([
        _ra("m", Priority.MUST_HAVE, 95),
        _ra("p", Priority.STRONG_PREFERENCE, 90),
    ])
    assert low < 25
    assert high > 85
    assert high - low > 50


def test_missing_must_have_drags_hard_but_not_gated():
    # match_score=0 on the must-have (graded), prefs strong. The 0.65 must-share
    # drags it well down but doesn't hard-zero (prefs still contribute 0.35).
    reqs = [_ra("m", Priority.MUST_HAVE, 0, status=Status.MISSING)] + [
        _ra(f"p{i}", Priority.STRONG_PREFERENCE, 90) for i in range(4)
    ]
    score = compute_requirements_match_score(reqs)
    # 0.65*0 + 0.35*0.9 = 0.315 → ~31.
    assert 25 <= score <= 40
