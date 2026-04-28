"""Fairness instrumentation: counterfactual probes, drift, conformal.

Module map:
- ``probes``        — generate counterfactual CV variants (name / school /
                       zip / graduation year swaps) from a base CV.
- ``impact_ratio``  — per-segment selection rate, scoring rate, impact
                       ratios computed over a rolling window.
- ``drift``         — Population Stability Index (PSI) on score
                       distributions per role family.
- ``conformal``     — split-CP deferral: when the calibrated interval
                       crosses the hire/no-hire boundary, mark the case
                       ``requires_human_review``.
"""

from .probes import (
    Probe,
    generate_probes,
    pairwise_flip_rate,
    score_delta,
)

__all__ = [
    "Probe",
    "generate_probes",
    "pairwise_flip_rate",
    "score_delta",
]
