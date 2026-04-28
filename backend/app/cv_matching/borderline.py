"""Borderline detection + self-consistency for the v4.3 path.

When a v4.2 score lands in [40, 75] it's borderline — the rubric
disambiguates poorly there. This module runs two complementary
disambiguators:

1. **Self-consistency** (CISC-style; arXiv 2502.06233): re-sample the
   v4.2 prompt at temperature=0.7 ``n=5`` times, compute mean and
   stddev. Surface `score = mean ± std` so the recruiter sees the
   uncertainty band. Stop early once the running stddev stabilises.

2. **Pairwise tie-break**: see ``pairwise.py``. Compare against
   per-archetype anchor candidates via PandaLM-consistency Haiku
   calls and back out a Bradley-Terry continuous score.

Both have their own cost guardrails so they don't 5× the per-match
spend when invoked.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger("taali.cv_match.borderline")


def is_borderline(role_fit: float, *, lo: float = 40.0, hi: float = 75.0) -> bool:
    return lo <= role_fit <= hi


@dataclass
class SelfConsistencyResult:
    samples: list[float]
    mean: float
    std: float
    early_stopped: bool


def _running_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    return math.sqrt(var)


def self_consistency(
    sampler,
    *,
    max_samples: int = 5,
    early_stop_tolerance: float = 0.5,
) -> SelfConsistencyResult:
    """Run a sampler ``n`` times, early-stop when stddev stabilises.

    ``sampler`` is a zero-arg callable that returns one role_fit_score.
    Production wires this to a re-call of ``run_cv_match`` at
    temperature=0.7 (see runner integration in RALPH 3.8).

    Stops early when the running stddev hasn't moved by more than
    ``early_stop_tolerance`` between consecutive iterations after the
    third sample.
    """
    samples: list[float] = []
    last_std = -1.0
    early_stopped = False
    for i in range(max_samples):
        try:
            samples.append(float(sampler()))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Self-consistency sampler failed at i=%d: %s", i, exc)
            break
        if len(samples) >= 3:
            cur_std = _running_std(samples)
            if abs(cur_std - last_std) < early_stop_tolerance:
                early_stopped = True
                break
            last_std = cur_std

    if not samples:
        return SelfConsistencyResult(
            samples=[], mean=0.0, std=0.0, early_stopped=False
        )

    mean = sum(samples) / len(samples)
    std = _running_std(samples)
    return SelfConsistencyResult(
        samples=samples, mean=mean, std=std, early_stopped=early_stopped
    )


__all__ = [
    "SelfConsistencyResult",
    "is_borderline",
    "self_consistency",
]
