"""HeuristicRetuner: caps, threshold checks, missing-signal weight bump."""

from __future__ import annotations

from app.decision_policy.feedback_aggregator import AggregatedSignals, Signal
from app.decision_policy.retuner import (
    MIN_SIGNALS_FOR_RETUNE,
    HeuristicRetuner,
)
from app.decision_policy.schema import PolicyJson


def _base_policy() -> PolicyJson:
    return PolicyJson.model_validate(
        {
            "schema_version": "v1",
            "decision_points": {
                "send_assessment": {
                    "thresholds": {
                        "role_fit_min": 65.0,
                        "pre_screen_min": 50.0,
                    },
                    "weights": {
                        "role_fit_score": 0.7,
                        "pre_screen_score": 0.3,
                    },
                    "rules": [
                        {
                            "if": "role_fit_score >= role_fit_min",
                            "then": "queue_send_assessment",
                            "priority": 50,
                        }
                    ],
                    "confidence_floor": 0.5,
                },
                "advance_to_interview": {
                    "thresholds": {"taali_score_min": 70.0},
                    "weights": {"taali_score": 1.0},
                    "rules": [],
                    "confidence_floor": 0.6,
                },
            },
        }
    )


def _signals(
    *,
    pattern: str,
    count: int,
    weight: float = 1.0,
    decision_point: str | None = None,
    signal_type: str = "manual",
) -> list[Signal]:
    return [
        Signal(
            signal_type=signal_type,
            weight=weight,
            disagreement_pattern=pattern,
            source_id=i + 1,
            decision_point=decision_point,
        )
        for i in range(count)
    ]


def test_retuner_skips_under_min_signals():
    retuner = HeuristicRetuner()
    aggregated = AggregatedSignals(
        organization_id=1,
        since=__import__("datetime").datetime(2026, 5, 1, tzinfo=__import__("datetime").timezone.utc),
        signals=_signals(
            pattern="manual-send-on-would-reject", count=2, decision_point="send_assessment"
        ),
    )
    proposal = retuner.propose(_base_policy(), aggregated)
    assert proposal is None


def test_loosen_role_fit_min_on_manual_send_pattern():
    retuner = HeuristicRetuner(min_signals=1)
    aggregated = AggregatedSignals(
        organization_id=1,
        since=__import__("datetime").datetime(2026, 5, 1, tzinfo=__import__("datetime").timezone.utc),
        signals=_signals(
            pattern="manual-send-on-would-reject",
            count=12,
            weight=0.8,
            decision_point="send_assessment",
        ),
    )
    proposal = retuner.propose(_base_policy(), aggregated)
    assert proposal is not None
    role_fit_min = proposal.new_policy_json["decision_points"]["send_assessment"][
        "thresholds"
    ]["role_fit_min"]
    # Loosened: should be < 65.
    assert role_fit_min < 65.0


def test_shifts_capped_at_max_per_dimension():
    retuner = HeuristicRetuner(min_signals=1, max_shift_per_dimension=5.0)
    # 1000 signals × weight 1 — magnitude saturates at max_shift.
    aggregated = AggregatedSignals(
        organization_id=1,
        since=__import__("datetime").datetime(2026, 5, 1, tzinfo=__import__("datetime").timezone.utc),
        signals=_signals(
            pattern="manual-send-on-would-reject",
            count=1000,
            weight=1.0,
            decision_point="send_assessment",
        ),
    )
    proposal = retuner.propose(_base_policy(), aggregated)
    role_fit_min = proposal.new_policy_json["decision_points"]["send_assessment"][
        "thresholds"
    ]["role_fit_min"]
    # Capped at 65 - 5.
    assert role_fit_min == 60.0


def test_missing_signal_failure_bumps_graph_prior_weight():
    retuner = HeuristicRetuner(min_signals=1)
    aggregated = AggregatedSignals(
        organization_id=1,
        since=__import__("datetime").datetime(2026, 5, 1, tzinfo=__import__("datetime").timezone.utc),
        signals=_signals(
            pattern="failure-mode:missing_signal",
            count=15,
            weight=1.0,
            signal_type="teach",
        ),
    )
    proposal = retuner.propose(_base_policy(), aggregated)
    assert proposal is not None
    weights = proposal.new_policy_json["decision_points"]["send_assessment"]["weights"]
    assert weights.get("graph_prior_p_advance", 0.0) > 0.0
    # Sum stays 1.0 (within float tolerance).
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_metadata_records_provenance():
    retuner = HeuristicRetuner(min_signals=1)
    aggregated = AggregatedSignals(
        organization_id=1,
        since=__import__("datetime").datetime(2026, 5, 1, tzinfo=__import__("datetime").timezone.utc),
        signals=_signals(
            pattern="manual-send-on-would-reject",
            count=15,
            weight=1.0,
            decision_point="send_assessment",
        ),
        teach_count=0,
        manual_count=15,
        override_count=0,
    )
    proposal = retuner.propose(_base_policy(), aggregated)
    metadata = proposal.new_policy_json["metadata"]
    assert metadata["trained_from_manual_decision_count"] == 15
    assert metadata["trained_at"]  # non-empty ISO string
