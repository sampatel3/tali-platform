"""Tests for fairness probes (RALPH 4.1) and harness gate plumbing
(RALPH 4.2 / 4.3).

The pure-function pieces (probe generation, flip rate, score delta)
are exercised here. The full harness end-to-end probe run is in the
``fairness.yml`` workflow because it requires a live Anthropic key.
"""

from __future__ import annotations

from app.cv_matching.fairness.probes import (
    Probe,
    generate_probes,
    pairwise_flip_rate,
    score_delta,
)


def test_generate_probes_default_returns_eight_variants():
    cv = "Sarah Johnson\nSenior Python engineer with 6 years experience\nUniversity of Michigan, 2018"
    probes = generate_probes("case_1", cv, n=8)
    assert len(probes) == 8
    # All probes are unique by probe_id.
    assert len({p.probe_id for p in probes}) == 8


def test_generate_probes_swaps_name_in_first_four():
    # Use a baseline name *not* in the swap pool to keep assertions tight.
    cv = "Alex Quinn\nPython engineer"
    probes = generate_probes("case_1", cv, n=8)
    name_only = [p for p in probes if p.swap_attribute == "name"]
    assert len(name_only) == 4
    for probe in name_only:
        assert "Alex Quinn" not in probe.cv_text


def test_generate_probes_combined_swaps_in_last_four():
    cv = "Alex Quinn\nPython engineer\nState University of Iowa, 2018"
    probes = generate_probes("case_1", cv, n=8)
    combined = [p for p in probes if p.swap_attribute == "name+school"]
    assert len(combined) == 4
    for probe in combined:
        assert "Alex Quinn" not in probe.cv_text
        # The school detector finds the "State University of Iowa" string;
        # combined probes swap to either Harvard or U Michigan.
        assert ("Harvard" in probe.cv_text) or (
            "University of Michigan" in probe.cv_text
        )


def test_generate_probes_is_deterministic():
    cv = "Some CV text"
    a = generate_probes("c", cv, n=8)
    b = generate_probes("c", cv, n=8)
    assert [p.probe_id for p in a] == [p.probe_id for p in b]
    assert [p.cv_text for p in a] == [p.cv_text for p in b]


def test_generate_probes_n_other_than_8_raises():
    try:
        generate_probes("c", "x", n=4)
    except NotImplementedError:
        return
    raise AssertionError("expected NotImplementedError for unsupported n")


def test_pairwise_flip_rate_zero_when_all_same():
    assert pairwise_flip_rate(["yes", "yes", "yes"]) == 0.0


def test_pairwise_flip_rate_max_when_two_classes_split():
    # 2 yes, 2 no → pairs: yy, yn, yn, yn, yn, nn = 4 flips of 6 pairs = 0.6667
    rate = pairwise_flip_rate(["yes", "yes", "no", "no"])
    assert abs(rate - (4.0 / 6.0)) < 1e-9


def test_pairwise_flip_rate_single_value_returns_zero():
    assert pairwise_flip_rate(["yes"]) == 0.0
    assert pairwise_flip_rate([]) == 0.0


def test_score_delta_zero_when_all_equal():
    mean_d, max_d = score_delta([70.0, 70.0, 70.0])
    assert mean_d == 0.0
    assert max_d == 0.0


def test_score_delta_normalised_to_unit_scale():
    """Mean of inputs is 60. Deltas: |70-60|=10 and |50-60|=10, both
    /100 = 0.10. Mean and max delta both 0.10 on the [0, 1] scale."""
    mean_d, max_d = score_delta([70.0, 50.0])
    assert abs(mean_d - 0.10) < 1e-9
    assert abs(max_d - 0.10) < 1e-9


def test_score_delta_ralph_threshold_5percent():
    """RALPH spec: "mean |Δscore| ≤ 0.05 on a 0-1 scale". A 5-point
    spread on a 0-100 scale equals 0.025 — well under threshold."""
    mean_d, _ = score_delta([72.0, 70.0, 68.0])  # spread of ±2
    assert mean_d < 0.05


def test_pairwise_flip_rate_ralph_threshold_5percent_is_strict():
    """RALPH spec: "no base case shows pairwise flip rate > 5%".
    With 8 probes / C(8,2)=28 pairs, even a single divergent probe
    contributes 7 mixed pairs / 28 = 25% flip rate — well above 5%.
    The 5% threshold is therefore strict: effectively "all 8 must
    agree". This test pins that interpretation so a future loosening
    of the assertion has to update it deliberately."""
    eight_all_same = ["yes"] * 8
    assert pairwise_flip_rate(eight_all_same) == 0.0

    eight_one_diverges = ["yes"] * 7 + ["no"]
    # 7 mixed pairs out of 28 = 0.25
    assert abs(pairwise_flip_rate(eight_one_diverges) - 0.25) < 1e-9
    # 0.25 > 0.05 — the gate fires.
    assert pairwise_flip_rate(eight_one_diverges) > 0.05
