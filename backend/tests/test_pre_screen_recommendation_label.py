"""pre_screen_recommendation_label respects the role's reject cutoff.

The "Below threshold" verdict used to be a hard-coded ``< 50``, so a role
that rejects at 30 would brand a 40-scorer "Below threshold" even though
they're above the role's bar. The label now takes the role threshold;
omitting it preserves the legacy ``< 50`` boundary for callers (like the
bulk directory snapshot fallback) that don't have the role in hand.
"""

from __future__ import annotations

from app.services.pre_screening_snapshot import pre_screen_recommendation_label


def test_none_score_returns_none():
    assert pre_screen_recommendation_label(None) is None
    assert pre_screen_recommendation_label(None, 30) is None


def test_legacy_behavior_when_no_threshold():
    assert pre_screen_recommendation_label(85) == "Strong match"
    assert pre_screen_recommendation_label(70) == "Proceed to screening"
    assert pre_screen_recommendation_label(55) == "Manual review recommended"
    assert pre_screen_recommendation_label(40) == "Below threshold"
    assert pre_screen_recommendation_label(20) == "Below threshold"


def test_threshold_below_50_does_not_brand_above_cutoff_as_below_threshold():
    # Role rejects at 30. A 40-scorer is above the bar → not "Below threshold".
    assert pre_screen_recommendation_label(40, 30) == "Manual review recommended"
    assert pre_screen_recommendation_label(35, 30) == "Manual review recommended"
    # Genuinely below the role's cutoff.
    assert pre_screen_recommendation_label(20, 30) == "Below threshold"
    # Boundary: a score exactly at the threshold is not below it.
    assert pre_screen_recommendation_label(30, 30) == "Manual review recommended"


def test_threshold_takes_precedence_over_quality_bands():
    # Strict role (cutoff 85): an 82 is below the bar even though it clears
    # the generic "Strong match" 80 band.
    assert pre_screen_recommendation_label(82, 85) == "Below threshold"
    # Above the strict cutoff still earns its quality-band label.
    assert pre_screen_recommendation_label(90, 85) == "Strong match"


def test_upper_quality_bands_unchanged_with_threshold():
    assert pre_screen_recommendation_label(85, 30) == "Strong match"
    assert pre_screen_recommendation_label(70, 30) == "Proceed to screening"
    assert pre_screen_recommendation_label(55, 30) == "Manual review recommended"
