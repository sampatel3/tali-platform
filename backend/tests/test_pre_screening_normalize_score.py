"""normalize_score_100 doesn't inflate already-0-100 values.

History:
  * PR #111: original heuristic auto-scaled anything ``<= 10`` by 10×,
    colliding with the fraud cap (10.0) — fraud-capped CVs ranked top.
  * PR #140 tightened to ``<= 1.0 → ×100`` only.
  * This PR drops the auto-scale entirely after Codex flagged that real
    aggregate ``role_fit`` scores can fall in (0, 1] (e.g. 0.4), and the
    ``<= 1.0`` rule was the same bug at a smaller magnitude — hiding
    near-zero candidates as moderate fits.

Every caller passes a value that's 0-100 by construction; just clamp.
"""

from __future__ import annotations

from app.platform.config import settings
from app.services.pre_screening_service import normalize_score_100


def test_fraud_cap_value_stays_at_cap_not_doubled():
    """The configured fraud cap must round-trip through normalize."""
    cap = float(settings.FRAUD_PENALTY_CAP_SCORE)
    assert normalize_score_100(cap) == cap


def test_zero_to_one_fraction_is_not_inflated():
    """A real aggregate score of 0.85 stays 0.85, not 85.

    Aggregated columns (cv_match_score, role_fit_score, etc.) can
    legitimately produce sub-1 values for very weak candidates.
    """
    assert normalize_score_100(0.85) == 0.8  # banker's rounding
    assert normalize_score_100(0.4) == 0.4
    assert normalize_score_100(1.0) == 1.0


def test_single_digit_score_no_longer_multiplied():
    """A genuine 0-100 value of 5 stays at 5, not 50."""
    assert normalize_score_100(5) == 5.0
    assert normalize_score_100(7.5) == 7.5


def test_zero_and_negative_handled():
    assert normalize_score_100(0) == 0.0
    assert normalize_score_100(-3) is None
    assert normalize_score_100(None) is None
    assert normalize_score_100("not a number") is None


def test_high_scores_clamped_to_100():
    assert normalize_score_100(120) == 100.0
