"""Platt scaling: logistic regression of raw → P(advance).

Pure-Python implementation (no numpy/sklearn dependency). Uses
batch gradient descent with L2 regularisation. Sufficient for the
small-data regime (N < 1000) per the RALPH spec; isotonic takes
over above that threshold.

Model: P(advance | x) = 1 / (1 + exp(-(a*x + b))).

State serialises to {"kind": "platt", "a": float, "b": float,
"feature_scale": float, "feature_shift": float}.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


def _sigmoid(z: float) -> float:
    # Numerically stable for very negative z.
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


@dataclass
class PlattCalibrator:
    """Logistic regression with one feature (the raw score).

    ``a`` and ``b`` parametrise the logit; ``feature_scale`` and
    ``feature_shift`` standardise the input so gradient descent
    converges quickly without the caller having to know the input
    range.
    """

    a: float = 0.0
    b: float = 0.0
    feature_scale: float = 1.0
    feature_shift: float = 0.0

    def fit(
        self,
        X: Sequence[float],
        y: Sequence[bool],
        *,
        learning_rate: float = 0.1,
        n_iterations: int = 2000,
        l2: float = 0.001,
    ) -> "PlattCalibrator":
        if len(X) != len(y):
            raise ValueError(f"X and y length mismatch: {len(X)} vs {len(y)}")
        if not X:
            raise ValueError("Cannot fit on empty data")

        # Standardise features: (x - mean) / (max(stddev, 1e-9)).
        n = len(X)
        mean = sum(X) / n
        var = sum((x - mean) ** 2 for x in X) / n
        scale = math.sqrt(var) or 1.0
        self.feature_shift = mean
        self.feature_scale = scale

        x_std = [(xi - mean) / scale for xi in X]
        y_f = [1.0 if yi else 0.0 for yi in y]

        a, b = 0.0, 0.0
        for _ in range(n_iterations):
            # Predictions and gradient.
            grad_a, grad_b = 0.0, 0.0
            for xi, yi in zip(x_std, y_f):
                p = _sigmoid(a * xi + b)
                err = p - yi
                grad_a += err * xi
                grad_b += err
            grad_a = grad_a / n + l2 * a
            grad_b = grad_b / n  # no L2 on intercept
            a -= learning_rate * grad_a
            b -= learning_rate * grad_b

        self.a = a
        self.b = b
        return self

    def predict(self, x: float) -> float:
        x_std = (x - self.feature_shift) / (self.feature_scale or 1.0)
        return _sigmoid(self.a * x_std + self.b)

    def to_dict(self) -> dict:
        return {
            "kind": "platt",
            "a": self.a,
            "b": self.b,
            "feature_scale": self.feature_scale,
            "feature_shift": self.feature_shift,
        }

    @classmethod
    def from_dict(cls, blob: dict) -> "PlattCalibrator":
        if blob.get("kind") != "platt":
            raise ValueError(f"Expected platt, got {blob.get('kind')!r}")
        return cls(
            a=float(blob["a"]),
            b=float(blob["b"]),
            feature_scale=float(blob.get("feature_scale", 1.0)),
            feature_shift=float(blob.get("feature_shift", 0.0)),
        )
