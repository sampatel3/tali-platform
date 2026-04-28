"""Split conformal prediction (RALPH 4.6).

Wraps a trained calibrator with a finite-sample prediction interval.
For a new score x with calibrated mean prediction p̂(x), the
conformal interval is

    [p̂(x) − q, p̂(x) + q]

where q is the (1 − α)-quantile of absolute residuals on the holdout
set. With α = 0.10, this is a 90% interval.

Deferral rule (RALPH 4.6 acceptance):

    if interval crosses the hire/no-hire decision boundary (default
    p = 0.5), mark the case ``requires_human_review = True``.

Pure-Python; no scipy.

Usage from the runner is opt-in via an injected ``ConformalPredictor``
instance. The calibrators module produces the calibrator; the
``ConformalPredictor`` wraps it and the holdout residuals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass
class ConformalPredictor:
    """Split-CP wrapper around a trained calibrator.

    ``residuals`` is the sorted list of absolute residuals
    ``|p̂(x_holdout) − y_holdout|`` from the calibration holdout set.
    The interval halfwidth at confidence level (1 − α) is the
    (1 − α)-quantile of the residual list (with a finite-sample
    correction).
    """

    residuals: list[float]
    alpha: float = 0.1
    decision_boundary: float = 0.5

    @classmethod
    def fit(
        cls,
        calibrator,
        X_holdout: Sequence[float],
        y_holdout: Sequence[bool],
        *,
        alpha: float = 0.1,
        decision_boundary: float = 0.5,
    ) -> "ConformalPredictor":
        if len(X_holdout) != len(y_holdout):
            raise ValueError("X / y length mismatch on holdout")
        if not X_holdout:
            return cls(residuals=[], alpha=alpha, decision_boundary=decision_boundary)
        residuals = []
        for x, y in zip(X_holdout, y_holdout):
            pred = calibrator.predict(x)
            target = 1.0 if y else 0.0
            residuals.append(abs(pred - target))
        residuals.sort()
        return cls(
            residuals=residuals,
            alpha=alpha,
            decision_boundary=decision_boundary,
        )

    def halfwidth(self) -> float:
        """The (1 − α)-quantile of the holdout residuals.

        Uses the standard split-CP finite-sample correction:
        rank = ceil((n + 1) * (1 − α))
        """
        n = len(self.residuals)
        if n == 0:
            return 1.0  # empty holdout → degenerate full-range interval
        rank = max(1, math.ceil((n + 1) * (1.0 - self.alpha)))
        idx = min(rank - 1, n - 1)
        return self.residuals[idx]

    def interval(self, point_prediction: float) -> tuple[float, float]:
        """[lo, hi] interval clamped to [0, 1]."""
        h = self.halfwidth()
        return (
            max(0.0, point_prediction - h),
            min(1.0, point_prediction + h),
        )

    def requires_human_review(self, point_prediction: float) -> bool:
        """True when the interval crosses the decision boundary."""
        lo, hi = self.interval(point_prediction)
        return lo <= self.decision_boundary <= hi


__all__ = ["ConformalPredictor"]
