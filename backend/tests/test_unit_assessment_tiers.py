"""Central difficulty-tier model + CV-claim-consistency tell + contract."""
from __future__ import annotations

from app.components.scoring.tiers import compute_tier_reached, cv_claim_consistency
from app.services.task_spec_loader import _validate_tiers

TIERS = {
    "L1": {"label": "Baseline", "min_tests_ratio": 0.35},
    "L2": {"label": "Core", "min_tests_ratio": 0.85},
    "L3": {"label": "Judgment", "min_tests_ratio": 0.95, "requires_design": True},
}


def test_no_tiers_returns_empty():
    assert compute_tier_reached(None, tests_passed=5, tests_total=10, design_score_10=8) == {}


def test_below_baseline_is_L0():
    r = compute_tier_reached(TIERS, tests_passed=0, tests_total=11, design_score_10=None)
    assert r["reached"] == "L0"


def test_basics_reach_L1():
    r = compute_tier_reached(TIERS, tests_passed=5, tests_total=11, design_score_10=None)  # 0.45
    assert r["reached"] == "L1"


def test_core_reaches_L2_without_design():
    r = compute_tier_reached(TIERS, tests_passed=10, tests_total=11, design_score_10=3.0)  # 0.91, design weak
    assert r["reached"] == "L2"


def test_judgment_reaches_L3_only_with_design_resolved():
    full_no_design = compute_tier_reached(TIERS, tests_passed=11, tests_total=11, design_score_10=3.0)
    assert full_no_design["reached"] == "L2"   # L3 gated on design
    full_with_design = compute_tier_reached(TIERS, tests_passed=11, tests_total=11, design_score_10=9.0)
    assert full_with_design["reached"] == "L3"


def test_cv_claim_consistency_soft_signal_only_below_baseline():
    l0 = {"reached": "L0"}
    sig = cv_claim_consistency(l0, role_name="AWS Data Engineer")
    assert sig and sig["signal"] == "below_competency_baseline"
    assert sig["severity"] == "review"  # soft — never a hard reject
    assert "AWS Data Engineer" in sig["message"]
    assert cv_claim_consistency({"reached": "L2"}, role_name="x") is None
    assert cv_claim_consistency({}, role_name="x") is None


def test_contract_tier_validator():
    assert _validate_tiers({}) == []                      # optional
    assert _validate_tiers({"tiers": TIERS}) == []        # valid
    bad_design = {"tiers": {**TIERS, "L3": {"label": "J", "min_tests_ratio": 0.95}}}
    assert any("requires_design" in e for e in _validate_tiers(bad_design))
    bad_order = {"tiers": {"L1": {"label": "a", "min_tests_ratio": 0.9},
                           "L2": {"label": "b", "min_tests_ratio": 0.5},
                           "L3": {"label": "c", "min_tests_ratio": 0.95, "requires_design": True}}}
    assert any("must be >=" in e for e in _validate_tiers(bad_order))
    bad_ratio = {"tiers": {**TIERS, "L1": {"label": "a", "min_tests_ratio": 1.5}}}
    assert any("[0, 1]" in e for e in _validate_tiers(bad_ratio))
