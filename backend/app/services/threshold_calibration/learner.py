"""Learn the advance/reject threshold from (score, label) pairs.

Metric: **Youden's J** (= TPR − FPR, equivalently 2·balanced_accuracy − 1).
J weights both classes equally, which is what we need on a heavily imbalanced
set (~6% positive) — raw accuracy would just learn "reject everyone". The
chosen cut is the integer threshold ``t`` maximising J, where a candidate
``advances`` iff ``score ≥ t``.

Pooling for sparse roles: learn the org-wide threshold as the anchor, then for
a role that clears its own floor, shrink its threshold toward the org anchor
(James–Stein style) and clamp it so a weak role's pool can RAISE the bar freely
but only LOWER it slightly — the "don't let a weak role drag the bar down"
guardrail. Finally clamp to the absolute quality band ``[50, 85]``.

Pure Python (no numpy) — runs nightly, bounded by 101 integer cut points.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Floors: never learn a threshold from too-few examples (fall through to the
# existing heuristic instead of guessing).
_MIN_POSITIVES = 8
_MIN_NEGATIVES = 20

# Shrinkage of a per-role threshold toward the org anchor: w = n / (n + k).
_SHRINK_K = 50.0

# A role may RAISE the bar above org by up to _CLAMP_UP, but only LOWER it by
# _CLAMP_DOWN (anti "best of a bad bunch").
_CLAMP_DOWN = 5.0
_CLAMP_UP = 15.0

# Absolute quality band (reuse the established send-bar bounds).
_ABS_FLOOR = 50.0
_ABS_CEILING = 85.0

# Minimum move before we bother proposing a change vs the currently-effective value.
MIN_CHANGE = 2.0


@dataclass
class ThresholdFit:
    threshold: float
    youden_j: float
    balanced_accuracy: float
    n_positive: int
    n_negative: int
    base_rate: float
    curve: list[dict] = field(default_factory=list)


def learn_threshold(
    pairs: list[tuple[float, int]],
    *,
    min_pos: int = _MIN_POSITIVES,
    min_neg: int = _MIN_NEGATIVES,
) -> ThresholdFit | None:
    """Youden's-J-optimal integer threshold, or None if below the sample floor."""
    pos = [s for s, label in pairs if label == 1]
    neg = [s for s, label in pairs if label == 0]
    P, N = len(pos), len(neg)
    if P < min_pos or N < min_neg:
        return None

    best_t, best_j = 0, -2.0
    curve: list[dict] = []
    for t in range(0, 101):  # advance iff score >= t
        tp = sum(1 for s in pos if s >= t)
        fp = sum(1 for s in neg if s >= t)
        tpr = tp / P
        fpr = fp / N
        j = tpr - fpr
        if t % 5 == 0:  # keep the persisted curve light
            curve.append({"t": t, "tpr": round(tpr, 3), "fpr": round(fpr, 3), "j": round(j, 3)})
        if j > best_j:
            best_j, best_t = j, t

    return ThresholdFit(
        threshold=float(best_t),
        youden_j=float(best_j),
        balanced_accuracy=float((best_j + 1.0) / 2.0),
        n_positive=P,
        n_negative=N,
        base_rate=float(P / (P + N)),
        curve=curve,
    )


def clamp_absolute(t: float) -> float:
    return max(_ABS_FLOOR, min(_ABS_CEILING, t))


def shrink_and_clamp_to_org(t_role: float, n_role: int, t_org: float) -> tuple[float, float]:
    """Shrink a per-role threshold toward the org anchor and clamp it.

    Returns ``(final_threshold, shrink_weight)``. ``shrink_weight`` (0..1) is
    how much the role's own data was trusted vs the org anchor.
    """
    w = n_role / (n_role + _SHRINK_K)
    shrunk = w * t_role + (1.0 - w) * t_org
    # A weak role may raise the bar freely but only lower it slightly.
    clamped = max(t_org - _CLAMP_DOWN, min(t_org + _CLAMP_UP, shrunk))
    return clamp_absolute(clamped), w
