"""Unit tests for the dynamic Stage-1 pre-screen gate calibrator (shadow)."""

from types import SimpleNamespace

from app.services import auto_threshold_service as ats
from app.services import prescreen_gate_calibration as gc


# --------------------------------------------------------------------------
# _max_cut_within_budget — the core false-reject-budgeted search
# --------------------------------------------------------------------------
def test_max_cut_picks_highest_within_budget():
    # 10 would-clear candidates (full >= 55) all with high pre-screen scores,
    # 30 misfits down low. No positive sits in the [20,45] band, so the cut can
    # safely climb to the ceiling (filters all 30 misfits, 0% false rejects).
    pairs = [(float(p), 80.0) for p in (50, 55, 60, 62, 65, 70, 75, 80, 85, 90)]
    pairs += [(float(p), 20.0) for p in range(1, 31)]  # 30 misfits, pre 1..30
    res = gc._max_cut_within_budget(pairs, send_bar=55.0)
    assert res is not None
    cut, fr, filtered, P = res
    assert cut == gc.GATE_CEILING       # nothing forces it lower
    assert fr == 0.0
    assert P == 10
    assert round(filtered, 2) == round(30 / 40, 2)


def test_max_cut_pulled_down_by_a_would_clear_candidate_in_the_band():
    # Same, plus ONE would-clear candidate scoring low on pre-screen (pre 25).
    # Filtering at/above 26 would false-reject that real candidate (1/11 = 9%
    # >> 1% budget), so the cut must stop at 25.
    pairs = [(float(p), 80.0) for p in (50, 55, 60, 62, 65, 70, 75, 80, 85, 90)]
    pairs.append((25.0, 60.0))          # would clear (full 60 >= 55) but low pre
    pairs += [(float(p), 20.0) for p in range(1, 31)]
    res = gc._max_cut_within_budget(pairs, send_bar=55.0)
    assert res is not None
    cut, fr, _filtered, P = res
    assert cut == 25                    # can't climb past the would-clear at 25
    assert fr == 0.0
    assert P == 11


def test_max_cut_none_when_too_few_positives():
    pairs = [(80.0, 80.0), (70.0, 60.0), (65.0, 58.0)]  # only 3 would-clear
    pairs += [(float(p), 20.0) for p in range(1, 31)]
    assert gc._max_cut_within_budget(pairs, send_bar=55.0) is None


# --------------------------------------------------------------------------
# compute_gate_threshold — wiring (DB + send bar monkeypatched)
# --------------------------------------------------------------------------
def _role():
    return SimpleNamespace(organization_id=1, id=7)


def test_compute_gate_threshold_calibrated(monkeypatch):
    pairs = [(float(p), 80.0) for p in (50, 55, 60, 62, 65, 70, 75, 80, 85, 90)]
    pairs += [(float(p), 20.0) for p in range(1, 31)]
    monkeypatch.setattr(gc, "_org_pairs", lambda db, *, organization_id: pairs)
    monkeypatch.setattr(
        ats, "compute_role_fit_send_threshold",
        lambda db, *, role: SimpleNamespace(value=55),
    )
    rec = gc.compute_gate_threshold(db=object(), role=_role())
    assert rec.source == "calibrated"
    assert rec.value == gc.GATE_CEILING
    assert rec.n_positive == 10
    assert rec.fr_rate == 0.0


def test_compute_gate_threshold_insufficient_pairs(monkeypatch):
    monkeypatch.setattr(gc, "_org_pairs", lambda db, *, organization_id: [(70.0, 80.0)])
    rec = gc.compute_gate_threshold(db=object(), role=_role())
    assert rec.source == "insufficient_data"
    assert rec.value == gc.GATE_FLOOR


def test_compute_gate_threshold_insufficient_positives(monkeypatch):
    # Enough pairs, but too few would-clear → can't estimate the FR rate.
    pairs = [(70.0, 80.0), (65.0, 60.0)] + [(float(p), 10.0) for p in range(1, 40)]
    monkeypatch.setattr(gc, "_org_pairs", lambda db, *, organization_id: pairs)
    monkeypatch.setattr(
        ats, "compute_role_fit_send_threshold",
        lambda db, *, role: SimpleNamespace(value=55),
    )
    rec = gc.compute_gate_threshold(db=object(), role=_role())
    assert rec.source == "insufficient_data"
    assert rec.value == gc.GATE_FLOOR


def test_cached_wrapper_hits_cache(monkeypatch):
    calls = {"n": 0}

    def fake_compute(db, *, role):
        calls["n"] += 1
        return gc.GateThresholdRecommendation(
            value=33, source="calibrated", rationale="x",
            sample_size=99, n_positive=20, fr_rate=0.0, filtered_frac=0.3,
        )

    gc._cache.clear()
    monkeypatch.setattr(gc, "compute_gate_threshold", fake_compute)
    r1 = gc.compute_gate_threshold_cached(db=object(), role=_role())
    r2 = gc.compute_gate_threshold_cached(db=object(), role=_role())
    assert r1.value == r2.value == 33
    assert calls["n"] == 1  # second call served from the TTL cache
    gc._cache.clear()
