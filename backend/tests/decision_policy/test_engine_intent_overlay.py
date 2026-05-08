"""Intent strictness modifier shifts thresholds without persisting."""

from __future__ import annotations

from app.decision_policy.intent import apply_intent_overrides
from app.decision_policy.schema import PolicyJson


def _policy(role_fit_min: float = 60.0, max_shift: float = 20.0) -> PolicyJson:
    return PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "send_assessment": {
                    "thresholds": {"role_fit_min": role_fit_min},
                    "weights": {"role_fit_score": 1.0},
                    "rules": [
                        {
                            "if": "role_fit_score >= role_fit_min",
                            "then": "queue_send_assessment",
                            "priority": 50,
                        }
                    ],
                    "confidence_floor": 0.5,
                }
            },
            "intent_overrides": {
                "honor_strictness_modifiers": True,
                "max_threshold_shift": max_shift,
            },
        }
    )


def test_zero_strictness_is_noop():
    policy = _policy()
    overlaid, overrode = apply_intent_overrides(policy, {"strictness_modifier": 0.0})
    assert overrode is False
    assert overlaid is policy  # cheap sanity


def test_positive_strictness_raises_floor():
    policy = _policy(role_fit_min=60.0, max_shift=20.0)
    overlaid, overrode = apply_intent_overrides(
        policy, {"strictness_modifier": 0.5}
    )
    assert overrode is True
    new_min = overlaid.decision_points["send_assessment"].thresholds["role_fit_min"]
    # +0.5 strictness × 20 cap = +10 shift.
    assert new_min == 70.0


def test_negative_strictness_lowers_floor():
    policy = _policy(role_fit_min=60.0, max_shift=20.0)
    overlaid, _ = apply_intent_overrides(policy, {"strictness_modifier": -1.0})
    new_min = overlaid.decision_points["send_assessment"].thresholds["role_fit_min"]
    # -1.0 strictness × 20 cap = -20 shift.
    assert new_min == 40.0


def test_shift_capped_at_max_threshold_shift():
    policy = _policy(role_fit_min=60.0, max_shift=20.0)
    overlaid, _ = apply_intent_overrides(
        policy, {"strictness_modifier": 5.0}  # clamped to +1.0
    )
    new_min = overlaid.decision_points["send_assessment"].thresholds["role_fit_min"]
    # +1.0 * 20 = 20 shift (max).
    assert new_min == 80.0


def test_intent_overlay_does_not_mutate_input_policy():
    policy = _policy(role_fit_min=60.0)
    apply_intent_overrides(policy, {"strictness_modifier": 0.5})
    assert (
        policy.decision_points["send_assessment"].thresholds["role_fit_min"] == 60.0
    )


def test_threshold_overlay_disabled_via_config():
    body = {
        "schema_version": "v1",
        "decision_points": {
            "send_assessment": {
                "thresholds": {"role_fit_min": 60.0},
                "weights": {"role_fit_score": 1.0},
                "rules": [
                    {
                        "if": "role_fit_score >= role_fit_min",
                        "then": "queue_send_assessment",
                        "priority": 50,
                    }
                ],
                "confidence_floor": 0.5,
            }
        },
        "intent_overrides": {
            "honor_strictness_modifiers": False,
            "max_threshold_shift": 20.0,
        },
    }
    policy = PolicyJson.model_validate(body)
    overlaid, overrode = apply_intent_overrides(
        policy, {"strictness_modifier": 0.5}
    )
    assert overrode is False
    assert overlaid is policy
