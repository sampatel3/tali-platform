"""Isotonic regression via the Pool-Adjacent-Violators algorithm.

Pure-Python implementation. Produces a non-decreasing piecewise-constant
mapping raw → P(advance). Used for the large-data regime (N >= 1000) where
the additional flexibility over a logistic curve buys real signal.

Predict: linear interpolation between fitted breakpoints, clamped at the
endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


def _pav(x: list[float], y: list[float]) -> list[tuple[float, float]]:
    """Pool-adjacent-violators algorithm.

    Input: parallel x and y arrays already sorted by x ascending.
    Output: list of (x, y) breakpoints where y is non-decreasing.

    Standard textbook PAV — runs in O(n) amortised. Each block is
    represented by its rightmost x and the block's average y.
    """
    if not x:
        return []
    # Each block: (sum_y, count, last_x).
    blocks: list[list[float]] = []
    for xi, yi in zip(x, y):
        blocks.append([yi, 1.0, xi])
        # Merge while previous block's mean violates monotonicity.
        while len(blocks) >= 2 and (
            blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]
        ):
            sy2, c2, _ = blocks.pop()
            sy1, c1, lx1 = blocks.pop()
            blocks.append([sy1 + sy2, c1 + c2, max(lx1, xi)])

    # Convert blocks to (rightmost_x, mean_y) breakpoints. Note multiple
    # x values may share a single y when they were pooled together.
    breakpoints: list[tuple[float, float]] = []
    for sum_y, count, right_x in blocks:
        breakpoints.append((right_x, sum_y / count))
    return breakpoints


@dataclass
class IsotonicCalibrator:
    breakpoints: list[tuple[float, float]] = field(default_factory=list)

    def fit(
        self,
        X: Sequence[float],
        y: Sequence[bool],
    ) -> "IsotonicCalibrator":
        if len(X) != len(y):
            raise ValueError(f"X and y length mismatch: {len(X)} vs {len(y)}")
        if not X:
            raise ValueError("Cannot fit on empty data")

        # Sort by x ascending; convert y to floats (0/1).
        pairs = sorted(zip(X, y), key=lambda t: t[0])
        xs = [float(p[0]) for p in pairs]
        ys = [1.0 if p[1] else 0.0 for p in pairs]

        self.breakpoints = _pav(xs, ys)
        return self

    def predict(self, x: float) -> float:
        if not self.breakpoints:
            return 0.0
        # Clamp at endpoints.
        if x <= self.breakpoints[0][0]:
            return float(self.breakpoints[0][1])
        if x >= self.breakpoints[-1][0]:
            return float(self.breakpoints[-1][1])
        # Linear interpolation between adjacent breakpoints.
        for i in range(1, len(self.breakpoints)):
            x0, y0 = self.breakpoints[i - 1]
            x1, y1 = self.breakpoints[i]
            if x <= x1:
                if x1 == x0:
                    return float(y1)
                t = (x - x0) / (x1 - x0)
                return float(y0 + t * (y1 - y0))
        return float(self.breakpoints[-1][1])  # pragma: no cover

    def to_dict(self) -> dict:
        return {
            "kind": "isotonic",
            "breakpoints": [
                {"x": x, "y": y} for x, y in self.breakpoints
            ],
        }

    @classmethod
    def from_dict(cls, blob: dict) -> "IsotonicCalibrator":
        if blob.get("kind") != "isotonic":
            raise ValueError(f"Expected isotonic, got {blob.get('kind')!r}")
        bps = [
            (float(p["x"]), float(p["y"])) for p in blob.get("breakpoints", [])
        ]
        return cls(breakpoints=bps)
