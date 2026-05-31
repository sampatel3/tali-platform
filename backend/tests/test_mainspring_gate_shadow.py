"""Promotion-gate convergence shadow comparator (ADR-0010 cut #3).

Behind a flag, every promotion-gate run is also evaluated through mainspring's
vendored gate-decision seam and a gate-decision agreement diff is logged. These
lock: no-op when off; the compared (agree) vs disagreement statuses; and that it
never raises (must not affect the gate run).
"""
from __future__ import annotations

import logging

from app.platform.config import settings
from app.services.mainspring_gate_shadow import shadow_compare_gate
from vendor.mainspring_gate.seam import (
    STATUS_ACTIVE,
    STATUS_FAILED_GATE,
    STATUS_GATED,
    SubCheck,
    evaluate_gate,
)

_SHADOW_EVENTS = lambda caplog: [
    r for r in caplog.records if getattr(r, "event", None) == "mainspring_gate_shadow"
]


def _t():
    return SubCheck(passed=True)


def _f(reason="failed"):
    return SubCheck(passed=False, reasons=[reason])


# --- vendored seam: the pure mainspring composition --------------------------


def test_vendored_seam_passes_only_when_all_three_pass():
    # Mainspring composes passed = shadow ∧ holdout ∧ bias; not-auto-apply lands
    # a passing gate in GATED (awaiting human activation), not promoted.
    d = evaluate_gate(shadow=_t(), holdout=_t(), bias=_t(), auto_apply=False)
    assert d.passed and d.promoted is False and d.status == STATUS_GATED


def test_vendored_seam_auto_apply_promotes_to_active():
    d = evaluate_gate(shadow=_t(), holdout=_t(), bias=_t(), auto_apply=True)
    assert d.passed and d.promoted is True and d.status == STATUS_ACTIVE


def test_vendored_seam_any_fail_lands_failed_gate():
    d = evaluate_gate(shadow=_t(), holdout=_f(), bias=_t(), auto_apply=True)
    assert d.passed is False and d.promoted is False and d.status == STATUS_FAILED_GATE
    assert any("holdout:" in r for r in d.reasons)


# --- shadow comparator -------------------------------------------------------


def test_shadow_is_noop_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_GATE_SHADOW", False, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.gate.shadow"):
        shadow_compare_gate(
            policy_version_id=1, tali_passed=True,
            gold_passed=True, bias_passed=True, shadow_passed=True,
        )
    assert _SHADOW_EVENTS(caplog) == []


def test_shadow_logs_compared_when_gates_agree(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_GATE_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.gate.shadow"):
        # All three pass on both sides → both gates pass → agreement.
        shadow_compare_gate(
            policy_version_id=7, tali_passed=True,
            gold_passed=True, bias_passed=True, shadow_passed=True,
            auto_apply=True,
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "compared"
    assert evs[0].agree is True
    assert evs[0].mainspring_passed is True
    assert evs[0].mainspring_status == STATUS_ACTIVE


def test_shadow_logs_compared_when_both_fail(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_GATE_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.gate.shadow"):
        # A sub-check fails on both → both gates fail → still agreement.
        shadow_compare_gate(
            policy_version_id=8, tali_passed=False,
            gold_passed=True, bias_passed=False, shadow_passed=True,
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "compared" and evs[0].agree is True
    assert evs[0].mainspring_passed is False
    assert evs[0].mainspring_status == STATUS_FAILED_GATE


def test_shadow_logs_disagreement_when_verdicts_differ(caplog, monkeypatch):
    """When tali's composite verdict and mainspring's diverge on the same
    sub-checks, that's a 'disagreement' (parity gap), not a benign compare.
    Here mainspring would pass (all three sub-checks green) but tali's claimed
    composite verdict is False — the gates disagree."""
    monkeypatch.setattr(settings, "MAINSPRING_GATE_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.gate.shadow"):
        shadow_compare_gate(
            policy_version_id=9, tali_passed=False,
            gold_passed=True, bias_passed=True, shadow_passed=True,
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "disagreement"
    assert evs[0].tali_passed is False and evs[0].mainspring_passed is True


def test_shadow_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_GATE_SHADOW", True, raising=False)
    # Garbage that would break the comparison must be swallowed, never propagated.
    shadow_compare_gate(
        policy_version_id=object(), tali_passed="not-a-bool",
        gold_passed=None, bias_passed=object(), shadow_passed="x",
        auto_apply="maybe", tali_reasons=42,
    )
