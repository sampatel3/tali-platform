"""Tests for fairness impact-ratio computation (RALPH 4.4)."""

from __future__ import annotations

from app.cv_matching.fairness.impact_ratio import (
    AMBER_THRESHOLD,
    ApplicationOutcome,
    GREEN_THRESHOLD,
    compute_impact_ratios,
    compute_intersectional_ratios,
)


def _outcomes_advance(seg: str, n_total: int, n_advance: int):
    rows = []
    for i in range(n_total):
        rows.append(
            ApplicationOutcome(
                application_id=i,
                recommendation="yes" if i < n_advance else "no",
                scoring_status="ok",
            )
        )
    return rows, {i: seg for i in range(n_total)}


def test_single_segment_returns_one_row_no_impact_ratio():
    outcomes, segments = _outcomes_advance("all", n_total=10, n_advance=4)
    rows = compute_impact_ratios(outcomes, segments)
    assert len(rows) == 1
    row = rows[0]
    assert row.segment_key == "all"
    assert row.n_applications == 10
    assert row.n_advanced == 4
    assert abs(row.selection_rate - 0.4) < 1e-9
    # Reference segment has no impact_ratio.
    assert row.impact_ratio is None
    assert row.rag == "green"


def test_two_segments_compute_impact_ratio_against_reference():
    """Segment A: 4/10 advance = 40% selection rate.
    Segment B: 2/10 advance = 20% selection rate.
    Reference = A. B's impact ratio = 20%/40% = 0.5.
    0.5 < 0.80 → RED."""
    outcomes_a, _ = _outcomes_advance("A", 10, 4)
    outcomes_b, _ = _outcomes_advance("B", 10, 2)

    # Re-id B applications to avoid id collision with A.
    outcomes_b = [
        ApplicationOutcome(
            application_id=100 + o.application_id,
            recommendation=o.recommendation,
            scoring_status=o.scoring_status,
        )
        for o in outcomes_b
    ]
    segments = {**{i: "A" for i in range(10)}, **{i + 100: "B" for i in range(10)}}

    rows = compute_impact_ratios(outcomes_a + outcomes_b, segments)
    assert len(rows) == 2
    by_seg = {r.segment_key: r for r in rows}
    assert by_seg["A"].impact_ratio is None  # reference
    assert abs(by_seg["B"].impact_ratio - 0.5) < 1e-9
    assert by_seg["A"].rag == "green"
    assert by_seg["B"].rag == "red"


def test_amber_threshold_band_classifies_correctly():
    """Sel rate ratio of 0.82 → amber (≥ 0.80, < 0.85)."""
    outcomes_a, _ = _outcomes_advance("A", 100, 50)
    outcomes_b, _ = _outcomes_advance("B", 100, 41)
    outcomes_b = [
        ApplicationOutcome(
            application_id=200 + o.application_id,
            recommendation=o.recommendation,
            scoring_status=o.scoring_status,
        )
        for o in outcomes_b
    ]
    segments = {
        **{i: "A" for i in range(100)},
        **{i + 200: "B" for i in range(100)},
    }
    rows = compute_impact_ratios(outcomes_a + outcomes_b, segments)
    by_seg = {r.segment_key: r for r in rows}
    # 41/50 = 0.82 → amber
    assert abs(by_seg["B"].impact_ratio - 0.82) < 1e-9
    assert by_seg["B"].rag == "amber"


def test_intersectional_ratios():
    """Segment combinations like female/black. Just sanity check the
    keying works — the math is the same as single-axis."""
    outcomes = [
        ApplicationOutcome(application_id=i, recommendation="yes" if i < 3 else "no", scoring_status="ok")
        for i in range(10)
    ]
    segment_dict = {
        i: {"gender": "female" if i % 2 else "male", "race": "black" if i < 5 else "white"}
        for i in range(10)
    }
    rows = compute_intersectional_ratios(
        outcomes, segment_dict, axes=("gender", "race")
    )
    keys = {r.segment_key for r in rows}
    assert keys.issubset(
        {"male/black", "female/black", "male/white", "female/white"}
    )


def test_thresholds_are_what_the_spec_says():
    """RALPH success criterion 6: per-segment IR ≥ 0.85."""
    assert GREEN_THRESHOLD == 0.85
    assert AMBER_THRESHOLD == 0.80


def test_unscored_applications_lower_scoring_rate_independently():
    """A segment that's getting scored less often should show a lower
    scoring_rate even if its selection_rate looks fine."""
    outcomes = [
        ApplicationOutcome(application_id=i, recommendation=None, scoring_status="failed")
        for i in range(5)
    ] + [
        ApplicationOutcome(application_id=10 + i, recommendation="yes", scoring_status="ok")
        for i in range(5)
    ]
    segments = {**{i: "X" for i in range(5)}, **{10 + i: "X" for i in range(5)}}
    rows = compute_impact_ratios(outcomes, segments)
    row = rows[0]
    assert row.n_applications == 10
    assert row.n_scored == 5
    assert row.scoring_rate == 0.5
