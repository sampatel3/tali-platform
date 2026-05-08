"""Schema rejects bad shapes loudly."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.decision_policy.schema import PolicyJson


def _minimal(**overrides):
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
    }
    body.update(overrides)
    return body


def test_minimal_policy_validates():
    PolicyJson.model_validate(_minimal())


def test_unknown_decision_point_rejected():
    body = _minimal()
    body["decision_points"]["totally_made_up"] = body["decision_points"]["send_assessment"]
    with pytest.raises(ValidationError):
        PolicyJson.model_validate(body)


def test_unknown_rule_action_rejected():
    body = _minimal()
    body["decision_points"]["send_assessment"]["rules"][0]["then"] = "do_a_barrel_roll"
    with pytest.raises(ValidationError):
        PolicyJson.model_validate(body)


def test_weights_must_sum_to_one():
    body = _minimal()
    body["decision_points"]["send_assessment"]["weights"] = {
        "role_fit_score": 0.3,
        "pre_screen_score": 0.3,
    }
    with pytest.raises(ValidationError):
        PolicyJson.model_validate(body)


def test_extra_fields_forbidden():
    body = _minimal()
    body["mystery_extension"] = {"foo": "bar"}
    with pytest.raises(ValidationError):
        PolicyJson.model_validate(body)


def test_schema_version_pinned():
    body = _minimal()
    body["schema_version"] = "v999"
    with pytest.raises(ValidationError):
        PolicyJson.model_validate(body)
