"""Tests for PSI drift (RALPH 4.5) and conformal prediction (RALPH 4.6)."""

from __future__ import annotations

from app.cv_matching.fairness.conformal import ConformalPredictor
from app.cv_matching.fairness.drift import (
    ALERT_THRESHOLD,
    INVESTIGATE_THRESHOLD,
    population_stability_index,
)


# --------------------------------------------------------------------------- #
# PSI                                                                          #
# --------------------------------------------------------------------------- #


def test_psi_zero_when_distributions_identical():
    same = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    assert population_stability_index(same, same) < 1e-6


def test_psi_low_when_distributions_close():
    a = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    b = [12, 22, 32, 42, 52, 62, 72, 82, 92]
    psi = population_stability_index(a, b)
    assert psi < INVESTIGATE_THRESHOLD


def test_psi_high_when_distribution_shifts_to_high_scores():
    """All low scores yesterday vs all high scores today → big PSI."""
    yesterday = [5, 10, 15, 20, 22, 24, 8, 12, 18, 6]
    today = [80, 85, 90, 92, 95, 88, 91, 84, 87, 93]
    psi = population_stability_index(today, yesterday)
    assert psi > ALERT_THRESHOLD


def test_psi_zero_when_either_empty():
    assert population_stability_index([], [10, 20, 30]) == 0.0
    assert population_stability_index([10, 20, 30], []) == 0.0


# --------------------------------------------------------------------------- #
# Conformal                                                                    #
# --------------------------------------------------------------------------- #


class _ConstantCalibrator:
    """Returns a fixed prediction regardless of input."""

    def __init__(self, prediction: float):
        self.prediction = prediction

    def predict(self, _x: float) -> float:
        return self.prediction


def test_conformal_halfwidth_uses_quantile_with_finite_correction():
    """For α=0.1 and a 9-element residual list, rank = ceil(10*0.9) = 9 →
    halfwidth = the largest residual."""
    cal = _ConstantCalibrator(0.5)
    cp = ConformalPredictor.fit(
        cal,
        X_holdout=[0.0] * 9,
        y_holdout=[False, False, False, False, False, True, True, True, True],
        alpha=0.1,
    )
    # Residuals are |0.5 - 0|=0.5 (5 times) and |0.5 - 1|=0.5 (4 times).
    # Sorted residuals: [0.5]*9. Halfwidth = 0.5.
    assert abs(cp.halfwidth() - 0.5) < 1e-9


def test_conformal_requires_human_review_when_interval_crosses_boundary():
    cal = _ConstantCalibrator(0.5)  # right on the decision boundary
    cp = ConformalPredictor.fit(
        cal,
        X_holdout=[0.0] * 10,
        y_holdout=[False] * 10,
        alpha=0.1,
    )
    # Pred 0.5; halfwidth somewhere > 0; interval crosses 0.5.
    assert cp.requires_human_review(0.5)


def test_conformal_no_review_when_pred_far_above_boundary():
    cal = _ConstantCalibrator(0.95)
    cp = ConformalPredictor.fit(
        cal,
        X_holdout=[0.0] * 100,
        y_holdout=[True] * 100,  # all advanced → tight residuals
        alpha=0.1,
    )
    # Halfwidth tiny because predictions are nearly perfect.
    assert not cp.requires_human_review(0.95)


def test_conformal_interval_is_clamped_to_unit_range():
    cal = _ConstantCalibrator(0.95)
    cp = ConformalPredictor(residuals=[0.4] * 10, alpha=0.1)
    lo, hi = cp.interval(0.95)
    assert lo >= 0.0
    assert hi <= 1.0


def test_conformal_empty_holdout_returns_full_range():
    cp = ConformalPredictor.fit(
        _ConstantCalibrator(0.5), X_holdout=[], y_holdout=[]
    )
    assert cp.halfwidth() == 1.0
    # Full interval crosses any boundary.
    assert cp.requires_human_review(0.5)
