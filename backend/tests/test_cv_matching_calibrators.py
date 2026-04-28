"""Tests for ``app.cv_matching.calibrators`` (RALPH 3.1).

Pure-Python: no numpy/sklearn. Covers:
- Platt fit→predict round-trip on synthetic data
- Isotonic fit→predict round-trip
- Auto-selection: small N picks Platt, large N picks Isotonic
- JSON persistence round-trip
- ``apply_calibrator`` returns None when no snapshot exists
"""

from __future__ import annotations

import json
import math

from app.cv_matching.calibrators import (
    IsotonicCalibrator,
    PlattCalibrator,
    apply_calibrator,
    fit_calibrator,
    load_calibrator,
    save_calibrator,
)
from app.cv_matching.calibrators.api import _SNAPSHOT_DIR


# --------------------------------------------------------------------------- #
# Platt                                                                        #
# --------------------------------------------------------------------------- #


def test_platt_fits_separable_data():
    """High raw scores → high P(advance)."""
    X = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95]
    y = [False] * 5 + [True] * 5
    cal = PlattCalibrator().fit(X, y)
    p_low = cal.predict(15)
    p_high = cal.predict(85)
    assert p_low < 0.4
    assert p_high > 0.6
    assert p_low < p_high  # monotonic


def test_platt_handles_constant_label_data():
    """Edge case: all labels the same. Output should saturate, not crash."""
    cal = PlattCalibrator().fit([10, 20, 30], [True, True, True])
    # All-True training → predict ≈ 1 across the input range.
    assert cal.predict(20) > 0.5


def test_platt_round_trip_through_json():
    cal = PlattCalibrator().fit([10, 50, 90], [False, True, True])
    blob = cal.to_dict()
    serialised = json.loads(json.dumps(blob))
    restored = PlattCalibrator.from_dict(serialised)
    for x in [10, 50, 90]:
        assert abs(restored.predict(x) - cal.predict(x)) < 1e-9


def test_platt_to_dict_kind():
    cal = PlattCalibrator().fit([10, 90], [False, True])
    assert cal.to_dict()["kind"] == "platt"


# --------------------------------------------------------------------------- #
# Isotonic                                                                     #
# --------------------------------------------------------------------------- #


def test_isotonic_is_monotone():
    X = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    y = [False, False, True, False, True, True, False, True, True]
    cal = IsotonicCalibrator().fit(X, y)
    # Sample at many points; predicted curve must be non-decreasing.
    last = -1.0
    for x in range(0, 101, 5):
        p = cal.predict(float(x))
        assert p >= last - 1e-9, f"non-monotone at x={x}: {p} < {last}"
        last = p


def test_isotonic_endpoint_clamping():
    cal = IsotonicCalibrator().fit([10, 50, 90], [False, True, True])
    # Far outside the training range — clamps to the edge breakpoint.
    assert cal.predict(-100) == cal.predict(10)
    assert cal.predict(1000) == cal.predict(90)


def test_isotonic_round_trip_through_json():
    X = [10, 30, 50, 70, 90]
    y = [False, False, True, True, True]
    cal = IsotonicCalibrator().fit(X, y)
    blob = cal.to_dict()
    restored = IsotonicCalibrator.from_dict(json.loads(json.dumps(blob)))
    for x in X:
        assert abs(restored.predict(x) - cal.predict(x)) < 1e-9


# --------------------------------------------------------------------------- #
# fit_calibrator (auto-selection + persistence)                                #
# --------------------------------------------------------------------------- #


def _cleanup_snapshot(role_family: str, dimension: str) -> None:
    for path in _SNAPSHOT_DIR.glob(f"{role_family}_{dimension}_*.json"):
        path.unlink(missing_ok=True)


def test_fit_calibrator_selects_platt_for_small_n():
    role_family = "test_role_family_platt"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)

    X = list(range(0, 100, 10))  # 10 samples
    y = [False] * 5 + [True] * 5
    cal = fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)
    assert isinstance(cal, PlattCalibrator)

    loaded = load_calibrator(role_family, dimension)
    assert isinstance(loaded, PlattCalibrator)
    assert abs(loaded.predict(50) - cal.predict(50)) < 1e-9
    _cleanup_snapshot(role_family, dimension)


def test_fit_calibrator_selects_isotonic_for_large_n():
    role_family = "test_role_family_isotonic"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)

    n = 1000
    X = [i * 0.1 for i in range(n)]
    # Sigmoid-shaped truth so isotonic has signal to fit.
    y = [
        (1.0 / (1.0 + math.exp(-(x - 50.0) / 10.0))) > 0.5 for x in X
    ]
    cal = fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)
    assert isinstance(cal, IsotonicCalibrator)
    _cleanup_snapshot(role_family, dimension)


def test_apply_calibrator_returns_none_when_missing():
    assert apply_calibrator("nonexistent_role_family_xyz", "cv_fit", 50.0) is None


def test_apply_calibrator_round_trip():
    role_family = "test_apply_round_trip"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)

    X = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    y = [False, False, False, False, False, True, True, True, True]
    fit_calibrator(role_family=role_family, dimension=dimension, X=X, y=y)
    p_high = apply_calibrator(role_family, dimension, 90.0)
    p_low = apply_calibrator(role_family, dimension, 10.0)
    assert p_high is not None and p_low is not None
    assert p_high > p_low
    _cleanup_snapshot(role_family, dimension)


def test_save_calibrator_writes_timestamped_and_latest():
    role_family = "test_save"
    dimension = "cv_fit"
    _cleanup_snapshot(role_family, dimension)
    cal = PlattCalibrator().fit([10, 90], [False, True])
    save_calibrator(role_family, dimension, cal)
    files = list(_SNAPSHOT_DIR.glob(f"{role_family}_{dimension}_*.json"))
    # One timestamped + one latest.
    assert len(files) == 2
    names = {f.name for f in files}
    assert any(n.endswith("_latest.json") for n in names)
    _cleanup_snapshot(role_family, dimension)
