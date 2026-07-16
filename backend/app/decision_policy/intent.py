"""Recruiter intent overlay — ephemeral, per-cycle, never persisted.

Intent enters the engine as a normalized dict from the active ``RoleIntent``.
The engine applies it as a third merge layer on top of the already-merged
(org default + role override) policy_json *only for the duration of one
``evaluate()`` call*.

Today the only thing the overlay does is shift thresholds inside each
decision point by the strictness modifier. Phase 2 grew ``must_skills``
and ``disqualifying_signals`` slots — those flow into the engine via
``DecisionInputs.flags`` (set by the orchestrator) rather than mutating
the policy.
"""

from __future__ import annotations

from typing import Any

from .schema import DecisionPoint, PolicyJson


# Tokens whose thresholds the strictness modifier *tightens* (raises) on
# +strictness and loosens (lowers) on -strictness. Keys not in this set
# are left alone so we don't accidentally invert semantics on a
# threshold whose direction is unknown.
_TIGHTENING_KEYS = frozenset(
    {
        "role_fit_min",
        "pre_screen_min",
        "taali_score_min",
        "assessment_score_min",
    }
)
_LOOSENING_KEYS = frozenset(
    {
        "role_fit_max",
        "pre_screen_max",
        "taali_score_max",
        "assessment_score_max",
    }
)


def apply_intent_overrides(
    policy: PolicyJson, intent: dict[str, Any]
) -> tuple[PolicyJson, bool]:
    """Return ``(overlaid_policy, intent_overrode)``.

    Always returns a *new* PolicyJson (model_copy) so callers can rely
    on immutability of the loaded row. ``intent_overrode`` is True iff
    any threshold was actually shifted.
    """
    if not intent or not policy.intent_overrides.honor_strictness_modifiers:
        return policy, False

    raw_modifier = intent.get("strictness_modifier")
    try:
        modifier = float(raw_modifier) if raw_modifier is not None else 0.0
    except (TypeError, ValueError):
        modifier = 0.0
    modifier = max(-1.0, min(1.0, modifier))
    if modifier == 0.0:
        return policy, False

    cap = float(policy.intent_overrides.max_threshold_shift)
    shift_magnitude = cap * abs(modifier)
    if shift_magnitude == 0.0:
        return policy, False

    overrode = False
    new_decision_points: dict[str, DecisionPoint] = {}
    for point_name, point in policy.decision_points.items():
        new_thresholds: dict[str, float] = dict(point.thresholds)
        for key, value in point.thresholds.items():
            if key in _TIGHTENING_KEYS:
                # +strictness -> raise the floor; -strictness -> lower it.
                delta = shift_magnitude if modifier > 0 else -shift_magnitude
                new_thresholds[key] = max(0.0, min(100.0, value + delta))
                if new_thresholds[key] != value:
                    overrode = True
            elif key in _LOOSENING_KEYS:
                # +strictness -> lower the cap (reject more); -strictness -> raise it.
                delta = -shift_magnitude if modifier > 0 else shift_magnitude
                new_thresholds[key] = max(0.0, min(100.0, value + delta))
                if new_thresholds[key] != value:
                    overrode = True
        if new_thresholds != point.thresholds:
            new_decision_points[point_name] = point.model_copy(
                update={"thresholds": new_thresholds}
            )
        else:
            new_decision_points[point_name] = point

    if not overrode:
        return policy, False

    overlaid = policy.model_copy(
        update={"decision_points": new_decision_points}
    )
    return overlaid, True


__all__ = ["apply_intent_overrides"]
