"""Abstention rule — Phase 4 §6.3 of the architecture spec.

Compose the four sub-agent outputs into a single yes/no/escalate
verdict. The composition is the bridge between the rule-driven
engine (which still owns deterministic short-circuits and the rule
list) and the fitted policy model (Phase 3, optional).

Three independent triggers any of which produces
``escalate_low_confidence``:

1. **Per-agent uncertainty** — if any sub-agent's uncertainty exceeds
   ``per_agent_uncertainty_threshold`` (default 0.5).
2. **Sub-agent disagreement** — if ``max(score) - median(score) > sharp_disagreement_delta``
   (default 0.5). This catches the case where some agents are
   confident "yes" and others confident "no" with the policy fitter
   in the middle.
3. **Calibrated confidence floor** — if the fitted policy (when
   available) emits ``max_class_probability < confidence_floor``
   (default 0.6). Below the floor we don't trust the rank.

When none of those trigger, the function returns ``None`` and the
caller proceeds with the rule-driven engine's verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# Hardcoded defaults. The Phase 5 promotion gate / policy config will
# expose these as a tunable per-org config; keep the defaults visible
# here so the implementation reads as a single function.
DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD = 0.5
DEFAULT_SHARP_DISAGREEMENT_DELTA = 0.5
DEFAULT_CONFIDENCE_FLOOR = 0.6


@dataclass
class AbstentionDecision:
    """Return shape from ``should_escalate``.

    Always carries the triggering reason in human-readable form so
    the queue UI can display "Escalated: pre_screen uncertainty=0.72"
    instead of just "no confident verdict".
    """

    escalate: bool
    reason: str | None = None
    triggered_by: str | None = None  # 'uncertainty' | 'disagreement' | 'confidence_floor'


def should_escalate(
    *,
    per_agent_scores: Sequence[float],
    per_agent_uncertainties: Sequence[float],
    calibrated_confidence: float | None,
    per_agent_uncertainty_threshold: float = DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD,
    sharp_disagreement_delta: float = DEFAULT_SHARP_DISAGREEMENT_DELTA,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    per_agent_names: Sequence[str] | None = None,
) -> AbstentionDecision:
    """Inspect sub-agent + policy outputs and decide whether to abstain.

    Returns ``AbstentionDecision(escalate=False)`` when none of the
    triggers fire — caller continues with the policy's main verdict.
    """
    names = list(per_agent_names or [f"agent_{i}" for i in range(len(per_agent_scores))])

    # 1. Per-agent uncertainty.
    for i, u in enumerate(per_agent_uncertainties):
        if u is None:
            continue
        if u >= per_agent_uncertainty_threshold:
            return AbstentionDecision(
                escalate=True,
                reason=(
                    f"{names[i] if i < len(names) else f'agent_{i}'} "
                    f"uncertainty {u:.2f} ≥ {per_agent_uncertainty_threshold:.2f}"
                ),
                triggered_by="uncertainty",
            )

    # 2. Sub-agent disagreement (max - median).
    valid_scores = [s for s in per_agent_scores if s is not None]
    if len(valid_scores) >= 3:
        sorted_scores = sorted(valid_scores)
        median = sorted_scores[len(sorted_scores) // 2]
        spread = max(valid_scores) - median
        if spread > sharp_disagreement_delta:
            return AbstentionDecision(
                escalate=True,
                reason=(
                    f"sub-agents disagree sharply (max-median spread "
                    f"{spread:.2f} > {sharp_disagreement_delta:.2f})"
                ),
                triggered_by="disagreement",
            )

    # 3. Calibrated confidence floor (only when a fitted policy spoke).
    if calibrated_confidence is not None:
        # The "max-class probability" framing: 0.5 means 50/50, 0.9
        # means strong yes/no. We treat the *distance from 0.5* as the
        # max-class proxy when the value is in [0, 1].
        max_class = max(calibrated_confidence, 1.0 - calibrated_confidence)
        if max_class < confidence_floor:
            return AbstentionDecision(
                escalate=True,
                reason=(
                    f"calibrated confidence {max_class:.2f} below floor "
                    f"{confidence_floor:.2f}"
                ),
                triggered_by="confidence_floor",
            )

    return AbstentionDecision(escalate=False)


__all__ = [
    "AbstentionDecision",
    "DEFAULT_CONFIDENCE_FLOOR",
    "DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD",
    "DEFAULT_SHARP_DISAGREEMENT_DELTA",
    "should_escalate",
]
