"""Agreement metrics for the v4 eval harness (RALPH 3.10).

Pure-Python (no scipy / sklearn). Sufficient for golden-case batch
sizes (tens of cases). For production-scale calibrator monitoring,
swap in the sklearn equivalents — the call signatures match.

Implemented:
- spearman_rho(predictions, ground_truth)
- cohens_kappa(predictions, ground_truth)        # categorical
- krippendorff_alpha_nominal(coders, items)      # nominal data
- brier_score(predictions, labels)
- expected_calibration_error(predictions, labels, n_bins=10)
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks (handles ties)."""
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda t: t[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman rank correlation. Returns 0.0 on degenerate input."""
    if len(x) != len(y):
        raise ValueError(f"length mismatch: {len(x)} vs {len(y)}")
    n = len(x)
    if n < 2:
        return 0.0
    rx = _ranks(x)
    ry = _ranks(y)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den_x = math.sqrt(sum((a - mean_x) ** 2 for a in rx))
    den_y = math.sqrt(sum((b - mean_y) ** 2 for b in ry))
    if den_x == 0.0 or den_y == 0.0:
        return 0.0
    return num / (den_x * den_y)


def cohens_kappa(a: Sequence, b: Sequence) -> float:
    """Cohen's κ for two raters on the same items.

    Categorical labels (any hashable). Returns 0.0 on degenerate
    input.
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return 0.0

    po = sum(1 for x, y in zip(a, b) if x == y) / n
    cat_a = Counter(a)
    cat_b = Counter(b)
    pe = sum(
        (cat_a[k] / n) * (cat_b.get(k, 0) / n)
        for k in set(list(cat_a) + list(cat_b))
    )
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def krippendorff_alpha_nominal(coders: Sequence[Sequence]) -> float:
    """Krippendorff α for nominal data, multiple coders.

    ``coders`` is a list of equal-length sequences, one per coder.
    Missing values can be encoded as None (skipped).

    Reference: Krippendorff 2011 "Computing Krippendorff's Alpha
    Reliability". This implementation handles the small-sample
    nominal case sufficient for v4 eval batches (~50 cases).
    """
    if not coders:
        return 0.0
    n_items = len(coders[0])
    if any(len(c) != n_items for c in coders):
        raise ValueError("All coders must have the same number of items")

    # Build per-item value lists.
    item_values: list[list] = []
    for i in range(n_items):
        vals = [c[i] for c in coders if c[i] is not None]
        item_values.append(vals)

    # Observed disagreement: average pairwise disagreement within items.
    n_pairs = 0
    obs_disagreement = 0.0
    for vals in item_values:
        m = len(vals)
        if m < 2:
            continue
        # Each unordered pair contributes 1 if the two values differ.
        for i in range(m):
            for j in range(i + 1, m):
                obs_disagreement += 0.0 if vals[i] == vals[j] else 1.0
                n_pairs += 1
    if n_pairs == 0:
        return 1.0  # vacuous agreement
    obs_disagreement /= n_pairs

    # Expected disagreement: pairwise disagreement over the global value pool.
    pool: list = []
    for vals in item_values:
        pool.extend(vals)
    p_n = len(pool)
    if p_n < 2:
        return 1.0
    counts = Counter(pool)
    same_pairs = sum(c * (c - 1) for c in counts.values())  # ordered same-pairs
    total_pairs = p_n * (p_n - 1)
    exp_agreement = same_pairs / total_pairs
    exp_disagreement = 1.0 - exp_agreement
    if exp_disagreement == 0.0:
        return 1.0
    return 1.0 - (obs_disagreement / exp_disagreement)


def brier_score(predictions: Sequence[float], labels: Sequence[bool]) -> float:
    if not predictions:
        return 0.0
    return sum(
        (p - (1.0 if y else 0.0)) ** 2 for p, y in zip(predictions, labels)
    ) / len(predictions)


def expected_calibration_error(
    predictions: Sequence[float],
    labels: Sequence[bool],
    *,
    n_bins: int = 10,
) -> float:
    """Standard ECE: weighted mean of |bin_acc - bin_conf|."""
    if not predictions:
        return 0.0
    n = len(predictions)
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, y in zip(predictions, labels):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    ece = 0.0
    for b in bins:
        if not b:
            continue
        conf = sum(p for p, _ in b) / len(b)
        acc = sum(1.0 for _, y in b if y) / len(b)
        ece += (len(b) / n) * abs(acc - conf)
    return ece


__all__ = [
    "brier_score",
    "cohens_kappa",
    "expected_calibration_error",
    "krippendorff_alpha_nominal",
    "spearman_rho",
]
