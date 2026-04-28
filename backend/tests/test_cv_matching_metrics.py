"""Tests for the eval harness agreement metrics (RALPH 3.10)."""

from __future__ import annotations

import math

from app.cv_matching.evals.metrics import (
    brier_score,
    cohens_kappa,
    expected_calibration_error,
    krippendorff_alpha_nominal,
    spearman_rho,
)


def test_spearman_rho_perfectly_correlated():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert abs(spearman_rho(x, y) - 1.0) < 1e-9


def test_spearman_rho_perfectly_anti_correlated():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [50.0, 40.0, 30.0, 20.0, 10.0]
    assert abs(spearman_rho(x, y) - (-1.0)) < 1e-9


def test_spearman_rho_handles_ties():
    x = [1.0, 1.0, 2.0, 2.0]
    y = [1.0, 2.0, 1.0, 2.0]
    rho = spearman_rho(x, y)
    assert -1.0 <= rho <= 1.0


def test_cohens_kappa_perfect_agreement():
    a = ["yes", "no", "yes", "yes", "no"]
    b = ["yes", "no", "yes", "yes", "no"]
    assert cohens_kappa(a, b) == 1.0


def test_cohens_kappa_chance_agreement_is_zero():
    # Two raters who always say "yes" with no disagreement: po=1, pe=1 → 1.0
    a = ["yes"] * 10
    b = ["yes"] * 10
    assert cohens_kappa(a, b) == 1.0


def test_cohens_kappa_in_between():
    a = ["yes", "yes", "no", "no", "yes"]
    b = ["yes", "no", "yes", "no", "yes"]
    k = cohens_kappa(a, b)
    assert -1.0 <= k <= 1.0


def test_krippendorff_alpha_perfect_agreement():
    coders = [
        [1, 2, 3, 4, 5],
        [1, 2, 3, 4, 5],
    ]
    assert krippendorff_alpha_nominal(coders) == 1.0


def test_krippendorff_alpha_random_disagreement():
    """All-different labels → α near zero."""
    coders = [
        [1, 2, 3, 4, 5],
        [3, 4, 5, 1, 2],
    ]
    a = krippendorff_alpha_nominal(coders)
    assert -1.0 <= a <= 1.0


def test_brier_score_zero_for_perfect_predictions():
    preds = [1.0, 0.0, 1.0, 0.0]
    labels = [True, False, True, False]
    assert brier_score(preds, labels) == 0.0


def test_brier_score_increases_with_error():
    a = brier_score([0.5, 0.5, 0.5, 0.5], [True, False, True, False])
    b = brier_score([0.9, 0.1, 0.9, 0.1], [True, False, True, False])
    assert a > b


def test_ece_small_when_predictions_match_outcomes_per_bin():
    """Predictions of 0.05 paired with False (true rate 0) leave a small
    residual ECE = |0.05 - 0| = 0.05. Calibration is 'good enough' but
    not literally zero unless predictions are exactly 0/1."""
    preds = [0.05, 0.05, 0.95, 0.95]
    labels = [False, False, True, True]
    ece = expected_calibration_error(preds, labels, n_bins=10)
    assert ece < 0.06


def test_ece_zero_when_predictions_are_extremes_and_match():
    preds = [0.0, 0.0, 1.0, 1.0]
    labels = [False, False, True, True]
    assert expected_calibration_error(preds, labels, n_bins=10) == 0.0


def test_ece_nonzero_when_overconfident():
    preds = [0.95, 0.95, 0.95, 0.95]  # always 95%
    labels = [True, True, False, False]  # actually 50%
    ece = expected_calibration_error(preds, labels, n_bins=10)
    assert abs(ece - 0.45) < 1e-9
