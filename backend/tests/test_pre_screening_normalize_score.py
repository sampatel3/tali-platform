"""normalize_score_100 doesn't double a fraud-capped score.

Codex flagged this on PR #111: the previous heuristic treated any
value ``<= 10`` as a 0-10 scale and multiplied by 10, which collided
with the fraud cap (10.0) — a fraud-capped candidate stored at 10
came back out as 100 and ranked as a top scorer. The fix tightens the
heuristic to ``<= 1.0`` (true 0-1 fractions only).
"""

from __future__ import annotations

from app.platform.config import settings
from app.services.pre_screening_service import normalize_score_100


def test_fraud_cap_value_stays_at_cap_not_doubled():
    """The configured fraud cap must round-trip through normalize."""
    cap = float(settings.FRAUD_PENALTY_CAP_SCORE)
    assert normalize_score_100(cap) == cap


def test_legitimate_zero_to_one_fraction_is_scaled():
    """0-1 fractions are still recognised — that's the only auto-scale case."""
    assert normalize_score_100(0.85) == 85.0
    assert normalize_score_100(1.0) == 100.0


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
