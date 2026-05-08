"""policy_diff: changed-only output, optional retuner annotation."""

from __future__ import annotations

from app.decision_policy.diff import policy_diff


def _policy(role_fit_min: float = 65.0) -> dict:
    return {
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
        "metadata": {"notes": "..."},
    }


def test_unchanged_returns_empty_diff():
    out = policy_diff(_policy(), _policy())
    assert out == {}


def test_threshold_change_surfaces():
    out = policy_diff(_policy(role_fit_min=65.0), _policy(role_fit_min=60.0))
    assert "decision_points.send_assessment.thresholds.role_fit_min" in out
    entry = out["decision_points.send_assessment.thresholds.role_fit_min"]
    assert entry["old"] == 65.0
    assert entry["new"] == 60.0


def test_metadata_changes_excluded():
    new = _policy()
    new["metadata"] = {"notes": "different"}
    out = policy_diff(_policy(), new)
    # Metadata-only changes don't surface (kept for retune log).
    assert out == {}
