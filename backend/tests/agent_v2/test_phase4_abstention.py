"""Phase 4 — escalate_low_confidence abstention.

The abstention rule has three independent triggers; this file exercises
each in isolation and the "all clear" path.
"""

from __future__ import annotations

from app.decision_policy.abstention import (
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD,
    DEFAULT_SHARP_DISAGREEMENT_DELTA,
    should_escalate,
)
from app.models.agent_decision import AGENT_DECISION_TYPES


# ---------------------------------------------------------------------------
# Decision type enum
# ---------------------------------------------------------------------------


def test_escalate_low_confidence_is_a_known_decision_type():
    assert "escalate_low_confidence" in AGENT_DECISION_TYPES


# ---------------------------------------------------------------------------
# Trigger 1: per-agent uncertainty
# ---------------------------------------------------------------------------


def test_escalates_when_any_agent_uncertainty_above_threshold():
    out = should_escalate(
        per_agent_scores=[0.8, 0.7, 0.6, 0.9],
        per_agent_uncertainties=[0.1, 0.2, 0.7, 0.1],  # third agent is uncertain
        calibrated_confidence=0.85,
        per_agent_names=["pre_screen", "cv_scoring", "assessment_scoring", "graph_priors"],
    )
    assert out.escalate is True
    assert out.triggered_by == "uncertainty"
    assert "assessment_scoring" in (out.reason or "")


def test_no_escalation_when_uncertainty_below_threshold():
    out = should_escalate(
        per_agent_scores=[0.8, 0.7, 0.75, 0.82],
        per_agent_uncertainties=[0.1, 0.2, 0.15, 0.1],
        calibrated_confidence=0.85,
    )
    assert out.escalate is False


# ---------------------------------------------------------------------------
# Trigger 2: sharp disagreement
# ---------------------------------------------------------------------------


def test_escalates_when_sub_agents_disagree_sharply():
    # 0.95, 0.4, 0.35, 0.3 → median ~0.375, max ~0.95 → spread > 0.5.
    out = should_escalate(
        per_agent_scores=[0.95, 0.4, 0.35, 0.3],
        per_agent_uncertainties=[0.1, 0.2, 0.2, 0.2],
        calibrated_confidence=0.7,
    )
    assert out.escalate is True
    assert out.triggered_by == "disagreement"


def test_no_disagreement_escalation_when_within_delta():
    out = should_escalate(
        per_agent_scores=[0.85, 0.7, 0.75, 0.8],
        per_agent_uncertainties=[0.1, 0.2, 0.15, 0.1],
        calibrated_confidence=0.8,
    )
    assert out.escalate is False


# ---------------------------------------------------------------------------
# Trigger 3: calibrated confidence floor
# ---------------------------------------------------------------------------


def test_escalates_when_calibrated_confidence_below_floor():
    # 0.55 → max_class = 0.55, below the 0.6 default floor.
    out = should_escalate(
        per_agent_scores=[0.6, 0.55, 0.5, 0.6],
        per_agent_uncertainties=[0.1, 0.1, 0.1, 0.1],
        calibrated_confidence=0.55,
    )
    assert out.escalate is True
    assert out.triggered_by == "confidence_floor"


def test_extreme_confidence_does_not_escalate_on_floor():
    # 0.1 means strong "no" — max_class = 0.9, above floor.
    out = should_escalate(
        per_agent_scores=[0.1, 0.05, 0.15, 0.08],
        per_agent_uncertainties=[0.1, 0.1, 0.1, 0.1],
        calibrated_confidence=0.1,
    )
    assert out.escalate is False


def test_no_calibrated_confidence_skips_floor_check():
    # When no fitted policy is available, the floor check shouldn't fire.
    out = should_escalate(
        per_agent_scores=[0.6, 0.55, 0.6, 0.65],
        per_agent_uncertainties=[0.1, 0.1, 0.1, 0.1],
        calibrated_confidence=None,
    )
    assert out.escalate is False


# ---------------------------------------------------------------------------
# Defaults exposed for the policy-config layer (Phase 5)
# ---------------------------------------------------------------------------


def test_defaults_match_spec_values():
    assert DEFAULT_PER_AGENT_UNCERTAINTY_THRESHOLD == 0.5
    assert DEFAULT_SHARP_DISAGREEMENT_DELTA == 0.5
    assert DEFAULT_CONFIDENCE_FLOOR == 0.6
