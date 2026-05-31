"""Bias/EEOC convergence shadow comparator (ADR-0010 cut #4).

Behind a flag, every bias audit is also scored through mainspring's vendored
bias seam and whether the two fairness verdicts agree is logged. These lock:
no-op when off; the compared / disagreement / unscorable statuses; and that it
never raises (must not affect the live audit / promotion gate).
"""
from __future__ import annotations

import logging

from app.platform.config import settings
from app.services.mainspring_bias_shadow import shadow_compare
from vendor.mainspring_bias.seam import (
    GroupRate,
    MAX_PARITY_GAP,
    evaluate_demographic_parity,
)

_SHADOW_EVENTS = lambda caplog: [
    r for r in caplog.records if getattr(r, "event", None) == "mainspring_bias_shadow"
]


def _metrics(attr: str, segs: dict[str, tuple[int, float]]) -> dict:
    """Build a tali-shaped metrics block: {attr: {seg: {n, selection_rate, ...}}}."""
    return {
        attr: {
            seg: {"n": n, "selection_rate": rate, "hire_rate": rate, "ece": 0.0}
            for seg, (n, rate) in segs.items()
        }
    }


# --- the vendored seam itself ------------------------------------------------


def test_vendored_seam_renders_a_demographic_parity_verdict():
    # Two balanced groups within the parity gap → passes.
    res = evaluate_demographic_parity(
        candidate_id=1,
        group_rates=[GroupRate("A", 10, 0.50), GroupRate("B", 10, 0.52)],
        global_positive_rate=0.51,
    )
    assert res.passed and res.n_groups == 2

    # A group far off the global rate → mainspring flags a violation.
    res2 = evaluate_demographic_parity(
        candidate_id=1,
        group_rates=[GroupRate("A", 10, 0.90), GroupRate("B", 10, 0.10)],
        global_positive_rate=0.50,
    )
    assert not res2.passed and res2.violations


def test_seam_skips_groups_under_min_n():
    # A group below MIN_GROUP_N is dropped, leaving <2 scored groups → no rule
    # fires, verdict passes (mirrors mainspring audit()).
    res = evaluate_demographic_parity(
        candidate_id=1,
        group_rates=[GroupRate("A", 10, 0.50), GroupRate("B", 2, 0.99)],
        global_positive_rate=0.55,
    )
    assert res.n_groups == 1 and res.passed


# --- the shadow comparator ---------------------------------------------------


def test_shadow_is_noop_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", False, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.bias.shadow"):
        shadow_compare(
            candidate_id=1,
            tali_passed=True,
            tali_metrics=_metrics("gender", {"F": (10, 0.50), "M": (10, 0.52)}),
            tali_violations=[],
        )
    assert _SHADOW_EVENTS(caplog) == []


def test_shadow_logs_compared_in_agreement(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", True, raising=False)
    # Balanced rates: both tali (passed) and mainspring (no parity violation) agree.
    with caplog.at_level(logging.INFO, logger="taali.bias.shadow"):
        shadow_compare(
            candidate_id=7,
            tali_passed=True,
            tali_metrics=_metrics("gender", {"F": (20, 0.50), "M": (20, 0.51)}),
            tali_violations=[],
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "compared"
    assert evs[0].candidate_id == 7
    assert evs[0].mainspring_passed is True
    assert evs[0].agreement is True


def test_shadow_logs_disagreement_when_verdicts_differ(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", True, raising=False)
    # tali says passed=True, but a wildly skewed group makes mainspring's
    # group-vs-global parity FAIL → the verdicts disagree.
    skew = _metrics("race", {"a": (20, 0.95), "b": (20, 0.05)})
    with caplog.at_level(logging.INFO, logger="taali.bias.shadow"):
        shadow_compare(
            candidate_id=9,
            tali_passed=True,
            tali_metrics=skew,
            tali_violations=[],
        )
    evs = _SHADOW_EVENTS(caplog)
    statuses = {e.status for e in evs}
    assert "compared" in statuses and "disagreement" in statuses
    compared = next(e for e in evs if e.status == "compared")
    assert compared.agreement is False and compared.mainspring_passed is False


def test_shadow_flags_unscorable_when_no_attr_has_two_scorable_segments(caplog, monkeypatch):
    """When no protected attribute has >= 2 segments each over MIN_GROUP_N,
    mainspring has nothing to score → 'unscorable' (a coverage gap, the bias
    analog of metering's 'unpriced'), not a misleading disagreement."""
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", True, raising=False)
    # One segment over MIN_GROUP_N, one under → not scorable.
    thin = _metrics("gender", {"F": (10, 0.50), "M": (2, 0.50)})
    # Also include tali's "insufficient_segments" marker shape to prove it's skipped.
    thin["age_band"] = {"status": "insufficient_segments", "segments": ["30s"]}
    with caplog.at_level(logging.INFO, logger="taali.bias.shadow"):
        shadow_compare(candidate_id=3, tali_passed=True, tali_metrics=thin, tali_violations=[])
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "unscorable"


def test_shadow_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", True, raising=False)
    # Garbage that would break scoring must be swallowed, never propagated.
    shadow_compare(
        candidate_id=None,
        tali_passed="maybe",
        tali_metrics={"gender": "not-a-dict", "race": None, "x": 123},
        tali_violations=None,
    )


# --- P2 FIX 3: undersized segments must count toward the GLOBAL parity baseline


def test_shadow_global_baseline_includes_undersized_segments(caplog, monkeypatch):
    """REGRESSION (P2 #3): mainspring skips undersized groups as violation
    CANDIDATES but still measures the scored groups against the GLOBAL
    population rate (which includes everyone). With many small high-rate
    segments + two scorable low-rate segments (the sparse `nationality` case),
    the real mainspring verdict FAILS. The old shadow computed the baseline from
    the scorable segments only → it logged a false pass/agreement. The fix folds
    the undersized segments into the global rate, so mainspring now correctly
    FAILS and the shadow logs a `disagreement` against tali's `passed=True`."""
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", True, raising=False)
    # 6 small high-rate segments (n=3 < MIN_GROUP_N, rate 0.90) lift the global
    # population rate to ~0.59; the two scorable low-rate segments (n=10, ~0.30)
    # then sit 0.29/0.27 below it — both > MAX_PARITY_GAP (0.15) → violations.
    segs = {f"small{i}": (3, 0.90) for i in range(6)}
    segs["bigA"] = (10, 0.30)
    segs["bigB"] = (10, 0.32)
    metrics = _metrics("nationality", segs)
    with caplog.at_level(logging.INFO, logger="taali.bias.shadow"):
        shadow_compare(
            candidate_id=11, tali_passed=True, tali_metrics=metrics, tali_violations=[]
        )
    evs = _SHADOW_EVENTS(caplog)
    statuses = {e.status for e in evs}
    assert "compared" in statuses and "disagreement" in statuses
    compared = next(e for e in evs if e.status == "compared")
    assert compared.mainspring_passed is False
    assert compared.agreement is False
    # Only the two scorable segments are EVALUATED as candidates; the small ones
    # are excluded from the violation set but counted in the baseline.
    assert compared.mainspring_violations == 2


def test_shadow_no_undersized_segments_unchanged(caplog, monkeypatch):
    """When every segment is scorable, the population baseline == the
    scorable-only baseline, so the verdict is unchanged: two balanced groups
    over MIN_GROUP_N still pass/agree (guards against an over-broad fix)."""
    monkeypatch.setattr(settings, "MAINSPRING_BIAS_SHADOW", True, raising=False)
    metrics = _metrics("gender", {"F": (20, 0.50), "M": (20, 0.51)})
    with caplog.at_level(logging.INFO, logger="taali.bias.shadow"):
        shadow_compare(
            candidate_id=12, tali_passed=True, tali_metrics=metrics, tali_violations=[]
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "compared"
    assert evs[0].mainspring_passed is True
    assert evs[0].agreement is True
