"""Bias verdict parity — mainspring seam == tali's original inline rule.

ADR-0010 cut #4 CUTOVER guard. Tali's ``decision_policy/bias_audit.audit`` now
delegates the fairness VERDICT (EEOC 4/5ths pairwise disparate-impact + the
selection / outcome / calibration parity gaps) to mainspring's vendored bias seam
(``pairwise_fairness_verdict``). This rule is compliance-signed-off, so the
relocation MUST be byte-identical — net ZERO behaviour change.

This test reconstructs tali's ORIGINAL inline verdict (lifted verbatim from
``git show origin/main:backend/app/decision_policy/bias_audit.py`` — the
``audit()`` body before the cutover) and asserts, over a representative corpus of
per-group-metric inputs (multiple protected attributes, group sizes, and
selection / outcome / calibration values, INCLUDING edge cases exactly at the
0.80 ratio and 0.05 / 0.07 gap boundaries), that the new seam verdict
``(metrics, violations)`` is IDENTICAL to the original. Any future drift in the
vendored seam fails CI here.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.decision_policy.bias_audit import BiasThresholds
from vendor.mainspring_bias.seam import (
    SegmentMetrics,
    BiasThresholds as SeamBiasThresholds,
    pairwise_fairness_verdict,
)


# ---------------------------------------------------------------------------
# tali's ORIGINAL inline verdict, lifted VERBATIM from
# origin/main:backend/app/decision_policy/bias_audit.py audit() — the
# pairwise loop over already-computed per-segment metrics. This is the
# compliance-signed-off reference the cutover must reproduce exactly.
# ---------------------------------------------------------------------------
def _original_verdict(
    *,
    metrics_in: dict[str, dict[str, dict]],
    thresholds: BiasThresholds,
) -> tuple[dict, list[dict]]:
    """Replays the pre-cutover ``audit()`` body, but fed pre-computed
    per-segment metrics instead of re-predicting (the metric computation is
    unchanged by the cutover and out of scope for the verdict-parity proof)."""
    metrics: dict = {}
    violations: list[dict] = []

    for attr in thresholds.protected_attributes:
        groups = metrics_in.get(attr, {})
        if len(groups) < 2:
            metrics[attr] = {"status": "insufficient_segments", "segments": list(groups.keys())}
            continue

        seg_summary: dict[str, dict] = {
            seg: {
                "n": s["n"],
                "selection_rate": s["selection_rate"],
                "hire_rate": s["hire_rate"],
                "ece": s["ece"],
            }
            for seg, s in groups.items()
        }
        metrics[attr] = seg_summary

        seg_names = list(groups.keys())
        for i, a in enumerate(seg_names):
            for b in seg_names[i + 1:]:
                ra = seg_summary[a]["selection_rate"] or 1e-9
                rb = seg_summary[b]["selection_rate"] or 1e-9
                dir_ratio = min(ra, rb) / max(ra, rb)
                if dir_ratio < thresholds.disparate_impact_ratio_min:
                    violations.append({
                        "attr": attr, "kind": "disparate_impact", "segments": [a, b],
                        "observed": dir_ratio, "threshold": thresholds.disparate_impact_ratio_min,
                    })
                sel_gap = abs(ra - rb)
                if sel_gap > thresholds.selection_rate_parity_max_gap:
                    violations.append({
                        "attr": attr, "kind": "selection_rate_gap", "segments": [a, b],
                        "observed": sel_gap, "threshold": thresholds.selection_rate_parity_max_gap,
                    })
                hire_gap = abs(seg_summary[a]["hire_rate"] - seg_summary[b]["hire_rate"])
                if hire_gap > thresholds.outcome_parity_max_gap:
                    violations.append({
                        "attr": attr, "kind": "outcome_gap", "segments": [a, b],
                        "observed": hire_gap, "threshold": thresholds.outcome_parity_max_gap,
                    })
                ece_gap = abs(seg_summary[a]["ece"] - seg_summary[b]["ece"])
                if ece_gap > thresholds.calibration_parity_max_gap:
                    violations.append({
                        "attr": attr, "kind": "calibration_gap", "segments": [a, b],
                        "observed": ece_gap, "threshold": thresholds.calibration_parity_max_gap,
                    })

    return metrics, violations


def _seam_verdict(
    *,
    metrics_in: dict[str, dict[str, dict]],
    thresholds: BiasThresholds,
) -> tuple[dict, list[dict]]:
    """Run the SAME per-segment metrics through the new vendored seam, exactly as
    the cutover ``audit()`` does (build SegmentMetrics in metric-list order)."""
    metrics_by_attr = {
        attr: [
            SegmentMetrics(
                segment=seg, n=s["n"], selection_rate=s["selection_rate"],
                hire_rate=s["hire_rate"], ece=s["ece"],
            )
            for seg, s in groups.items()
        ]
        for attr, groups in metrics_in.items()
    }
    seam_thr = SeamBiasThresholds(
        disparate_impact_ratio_min=thresholds.disparate_impact_ratio_min,
        selection_rate_parity_max_gap=thresholds.selection_rate_parity_max_gap,
        outcome_parity_max_gap=thresholds.outcome_parity_max_gap,
        calibration_parity_max_gap=thresholds.calibration_parity_max_gap,
        protected_attributes=tuple(thresholds.protected_attributes),
    )
    return pairwise_fairness_verdict(
        metrics_by_attr=metrics_by_attr,
        thresholds=seam_thr,
        protected_attributes=list(thresholds.protected_attributes),
    )


def _seg(n, sel, hire=0.0, ece=0.0):
    return {"n": n, "selection_rate": sel, "hire_rate": hire, "ece": ece}


# Default (compliance) thresholds + a few attributes to exercise iteration order.
THR = BiasThresholds(protected_attributes=("gender", "race", "age_band", "nationality"))


# A representative corpus. Each entry: {attr: {segment: seg_metrics}}.
CORPUS: list[dict] = [
    # 1. Perfectly balanced — passes.
    {"gender": {"F": _seg(30, 0.50, 0.50, 0.02), "M": _seg(30, 0.50, 0.50, 0.02)}},
    # 2. Severe disparate impact (DIR=0) + selection + outcome gaps.
    {"gender": {"F": _seg(20, 0.0, 0.0, 0.0), "M": _seg(20, 1.0, 1.0, 0.0)}},
    # 3. Multiple attributes, one clean one skewed.
    {
        "gender": {"F": _seg(40, 0.48, 0.50, 0.03), "M": _seg(40, 0.50, 0.52, 0.03)},
        "race": {"a": _seg(25, 0.90, 0.80, 0.10), "b": _seg(25, 0.30, 0.20, 0.01)},
    },
    # 4. Single segment on an attribute -> insufficient_segments, no violation.
    {"gender": {"F": _seg(15, 0.5, 0.5, 0.0)}},
    # 5. Three segments -> all C(3,2)=3 pairs evaluated.
    {"race": {"a": _seg(10, 0.90, 0.5, 0.0), "b": _seg(10, 0.70, 0.5, 0.0), "c": _seg(10, 0.50, 0.5, 0.0)}},
    # --- BOUNDARY CASES -----------------------------------------------------
    # 6. DIR exactly at 0.80 (0.40/0.50) — NOT < 0.80, so NO disparate_impact;
    #    but selection gap 0.10 > 0.05 fires.
    {"gender": {"F": _seg(20, 0.40, 0.5, 0.0), "M": _seg(20, 0.50, 0.5, 0.0)}},
    # 7. DIR just below 0.80 (0.399/0.50 = 0.798) — disparate_impact DOES fire.
    {"gender": {"F": _seg(20, 0.399, 0.5, 0.0), "M": _seg(20, 0.50, 0.5, 0.0)}},
    # 8. Selection gap EXACTLY 0.05 (0.50 vs 0.55) — NOT > 0.05, no selection
    #    violation; DIR 0.909 ok too -> clean.
    {"gender": {"F": _seg(20, 0.50, 0.5, 0.0), "M": _seg(20, 0.55, 0.5, 0.0)}},
    # 9. Selection gap just over 0.05 (0.50 vs 0.5501) -> selection_rate_gap.
    {"gender": {"F": _seg(20, 0.50, 0.5, 0.0), "M": _seg(20, 0.5501, 0.5, 0.0)}},
    # 10. Outcome gap EXACTLY 0.07 -> NOT > 0.07, no outcome violation.
    {"gender": {"F": _seg(20, 0.5, 0.50, 0.0), "M": _seg(20, 0.5, 0.57, 0.0)}},
    # 11. Outcome gap just over 0.07 -> outcome_gap fires.
    {"gender": {"F": _seg(20, 0.5, 0.50, 0.0), "M": _seg(20, 0.5, 0.5701, 0.0)}},
    # 12. Calibration (ECE) gap EXACTLY 0.05 -> NOT > 0.05, no calibration viol.
    {"gender": {"F": _seg(20, 0.5, 0.5, 0.02), "M": _seg(20, 0.5, 0.5, 0.07)}},
    # 13. Calibration gap just over 0.05 -> calibration_gap fires.
    {"gender": {"F": _seg(20, 0.5, 0.5, 0.02), "M": _seg(20, 0.5, 0.5, 0.0701)}},
    # 14. Zero selection rate on one segment (1e-9 floor path) -> DIR ~0.
    {"gender": {"F": _seg(20, 0.0, 0.5, 0.0), "M": _seg(20, 0.6, 0.5, 0.0)}},
    # 15. Both selection rates zero -> DIR = 1e-9/1e-9 = 1.0 -> no DI; all gaps 0.
    {"gender": {"F": _seg(20, 0.0, 0.5, 0.0), "M": _seg(20, 0.0, 0.5, 0.0)}},
    # 16. All four violation kinds fire at once on one pair.
    {"race": {"a": _seg(20, 0.20, 0.10, 0.01), "b": _seg(20, 0.90, 0.90, 0.30)}},
    # 17. Empty / unaudited attributes mixed in (nationality has no data).
    {
        "gender": {"F": _seg(20, 0.5, 0.5, 0.0), "M": _seg(20, 0.5, 0.5, 0.0)},
        "age_band": {"20s": _seg(10, 0.80, 0.5, 0.0), "40s": _seg(10, 0.55, 0.5, 0.0)},
    },
    # 18. Asymmetric group sizes (n doesn't affect the rate-based verdict).
    {"race": {"a": _seg(3, 0.90, 0.9, 0.2), "b": _seg(500, 0.30, 0.2, 0.01)}},
]


@pytest.mark.parametrize("metrics_in", CORPUS, ids=[f"case{i+1}" for i in range(len(CORPUS))])
def test_seam_verdict_is_byte_identical_to_original(metrics_in):
    orig_metrics, orig_violations = _original_verdict(metrics_in=metrics_in, thresholds=THR)
    seam_metrics, seam_violations = _seam_verdict(metrics_in=metrics_in, thresholds=THR)
    # passed verdict identical.
    assert (len(orig_violations) == 0) == (len(seam_violations) == 0)
    # The exact violation set (order included) identical.
    assert seam_violations == orig_violations
    # The metrics block (incl. insufficient_segments markers) identical.
    assert seam_metrics == orig_metrics


def test_full_audit_path_matches_original_over_corpus():
    """Sanity: across the whole corpus the passed/violations stream matches."""
    for metrics_in in CORPUS:
        o = _original_verdict(metrics_in=metrics_in, thresholds=THR)
        s = _seam_verdict(metrics_in=metrics_in, thresholds=THR)
        assert o == s


def test_default_thresholds_match_compliance_values():
    """The seam's thresholds must mirror the compliance-signed-off defaults so a
    silent threshold drift (a compliance event) fails CI."""
    seam = SeamBiasThresholds()
    assert seam.disparate_impact_ratio_min == 0.80
    assert seam.selection_rate_parity_max_gap == 0.05
    assert seam.outcome_parity_max_gap == 0.07
    assert seam.calibration_parity_max_gap == 0.05
