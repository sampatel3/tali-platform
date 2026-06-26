"""Unit tests for the pure comparison logic in the shadow re-score harness.

The DB + Anthropic plumbing is exercised in prod runs; here we lock down the
go/no-go maths (band flips, deltas, rank correlation) that decide whether a
scoring change is safe to flip.
"""
from __future__ import annotations

from scripts.shadow_rescore_assessments import (
    _spearman,
    band,
    compare_runs,
    summarize_comparisons,
)


def test_band_thresholds():
    assert band(None) == "none"
    assert band(0) == "poor"
    assert band(49.9) == "poor"
    assert band(50) == "good"
    assert band(79.9) == "good"
    assert band(80) == "excellent"
    assert band(100) == "excellent"


def test_compare_runs_overall_and_dimension_deltas():
    base = {"overall": 60.0, "dimensions": {"a": 6.0, "b": 7.0}}
    cand = {"overall": 72.0, "dimensions": {"a": 8.0, "b": 6.5}}
    cmp = compare_runs(101, base, cand)
    assert cmp["overall_delta"] == 12.0
    assert cmp["dimension_deltas"] == {"a": 2.0, "b": -0.5}
    # 60 (good) -> 72 (good): no band flip
    assert cmp["band_flip"] is False


def test_compare_runs_detects_band_flip():
    cmp = compare_runs(7, {"overall": 48.0, "dimensions": {}}, {"overall": 55.0, "dimensions": {}})
    assert cmp["band_flip"] is True
    assert cmp["baseline_band"] == "poor"
    assert cmp["candidate_band"] == "good"


def test_compare_runs_handles_missing_scores():
    cmp = compare_runs(1, {"overall": None, "dimensions": {}}, {"overall": 50.0, "dimensions": {}})
    assert cmp["overall_delta"] is None


def test_spearman_perfect_and_inverse():
    assert _spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert _spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert _spearman([1], [1]) is None  # too few points
    assert _spearman([5, 5, 5], [1, 2, 3]) is None  # degenerate (no variance)


def test_summarize_comparisons_aggregates():
    comparisons = [
        compare_runs(1, {"overall": 60.0, "dimensions": {}}, {"overall": 62.0, "dimensions": {}}),
        compare_runs(2, {"overall": 80.0, "dimensions": {}}, {"overall": 78.0, "dimensions": {}}),  # band flip excellent->good
        compare_runs(3, {"overall": 40.0, "dimensions": {}}, {"overall": 41.0, "dimensions": {}}),
    ]
    s = summarize_comparisons(comparisons)
    assert s["n"] == 3
    assert s["n_scored"] == 3
    assert s["mean_abs_delta"] == round((2 + 2 + 1) / 3, 2)
    assert s["max_abs_delta"] == 2.0
    assert s["n_band_flips"] == 1
    assert s["band_flip_ids"] == [2]
    # candidate order tracks baseline order → strong positive correlation
    assert s["rank_correlation"] == 1.0
