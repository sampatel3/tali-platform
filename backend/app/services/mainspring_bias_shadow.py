"""Shadow comparator for the bias/EEOC convergence (ADR-0010, cut #4).

Behind a flag (``MAINSPRING_BIAS_SHADOW``), every bias audit is ALSO scored
through mainspring's vendored bias seam, and whether the two *fairness verdicts*
(``passed``) agree is logged. No DB writes, no behaviour change — this is the
at-parity evidence ADR-0010 requires *before* any cutover. The vendored seam
lives under ``backend/vendor/mainspring_bias`` (mirror-vendored from mainspring
master; re-vendor via ``scripts/vendor_mainspring_bias.sh``).

The two engines do NOT share a fairness definition yet (see the seam's PARITY
NOTE): tali runs a PAIRWISE 4/5ths + selection/outcome/calibration-gap audit
across protected attributes; mainspring runs a group-vs-GLOBAL demographic-parity
check with a looser default gap. So the shadow does the only schema-faithful
thing it can today: per protected attribute it feeds the SAME per-segment
selection rates tali computed into mainspring's demographic-parity verdict, then
compares the overall boolean ``passed``.

Statuses logged distinctly so the parity log is actionable:
- ``compared``     — both engines rendered a verdict; ``agreement`` is whether
  tali's ``passed`` matched mainspring's. (A genuine, expected divergence given
  the different definitions — the gap to close in the schema mapping.)
- ``disagreement`` — convenience flag when the verdicts differ (logged as a
  ``compared`` event with ``agreement=False`` AND a dedicated ``disagreement``
  event, so either can be alerted on).
- ``unscorable``   — no protected attribute had >= 2 segments each over
  ``MIN_GROUP_N`` for mainspring to render a verdict (tali's ``insufficient_*``
  cases) → a coverage gap, not a real disagreement; flagged as its own status
  (the bias analog of metering's ``unpriced``).
- the comparison never raises — a shadow failure must not affect the live audit.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from ..platform.config import settings

logger = logging.getLogger("taali.bias.shadow")


def _segment_rates(seg_summary: Mapping[str, Any]) -> list[tuple[str, int, float]]:
    """Extract ``(segment, n, selection_rate)`` from one attribute's tali
    metrics block, skipping non-segment markers (``status`` / ``segments`` keys
    tali writes for ``insufficient_segments``)."""
    rows: list[tuple[str, int, float]] = []
    for seg, summ in seg_summary.items():
        if not isinstance(summ, Mapping) or "selection_rate" not in summ:
            continue
        try:
            rows.append((str(seg), int(summ.get("n", 0)), float(summ["selection_rate"])))
        except (TypeError, ValueError):
            continue
    return rows


def shadow_compare(
    *,
    candidate_id: int,
    tali_passed: bool,
    tali_metrics: Mapping[str, Any],
    tali_violations: Sequence[Any] = (),
) -> None:
    """If bias shadow is on, render mainspring's demographic-parity verdict on
    the same per-segment selection rates tali computed and log whether the two
    fairness verdicts agree. Never raises."""
    if not getattr(settings, "MAINSPRING_BIAS_SHADOW", False):
        return
    try:
        from vendor.mainspring_bias.seam import (
            GroupRate,
            MIN_GROUP_N,
            evaluate_demographic_parity,
        )

        # Per protected attribute, run mainspring's group-vs-global verdict on
        # tali's per-segment selection rates. The candidate fails mainspring's
        # audit if ANY attribute shows a parity violation — mainspring's audit()
        # aggregates the same way (one violations list across the audit set).
        ms_passed = True
        ms_violation_count = 0
        scored_attrs: list[str] = []
        for attr, block in tali_metrics.items():
            if not isinstance(block, Mapping):
                continue
            rows = _segment_rates(block)
            scorable = [(g, n, r) for (g, n, r) in rows if n >= MIN_GROUP_N]
            if len(scorable) < 2:
                # tali's "insufficient_segments" / too-small groups — mainspring
                # can't render a verdict on this attribute either.
                continue
            scored_attrs.append(attr)
            total_n = sum(n for _, n, _ in scorable)
            # Global positive rate = n-weighted mean of the segment rates (the
            # population selection rate mainspring's audit() compares against).
            global_rate = (
                sum(n * r for _, n, r in scorable) / total_n if total_n else 0.0
            )
            result = evaluate_demographic_parity(
                candidate_id=int(candidate_id),
                group_rates=[GroupRate(group=g, n=n, positive_rate=r) for g, n, r in scorable],
                global_positive_rate=global_rate,
            )
            ms_violation_count += len(result.violations)
            if not result.passed:
                ms_passed = False

        if not scored_attrs:
            # No attribute had >= 2 segments over MIN_GROUP_N — mainspring has
            # nothing to score. A coverage gap, not a real disagreement.
            logger.info(
                "mainspring_bias_shadow status=unscorable candidate_id=%s tali_passed=%s",
                candidate_id, tali_passed,
                extra={
                    "event": "mainspring_bias_shadow",
                    "status": "unscorable",
                    "candidate_id": int(candidate_id),
                    "tali_passed": bool(tali_passed),
                },
            )
            return

        agreement = bool(tali_passed) == bool(ms_passed)
        logger.info(
            "mainspring_bias_shadow status=compared candidate_id=%s tali_passed=%s "
            "mainspring_passed=%s agreement=%s tali_violations=%s mainspring_violations=%s "
            "scored_attrs=%s",
            candidate_id, tali_passed, ms_passed, agreement,
            len(tali_violations), ms_violation_count, scored_attrs,
            extra={
                "event": "mainspring_bias_shadow",
                "status": "compared",
                "candidate_id": int(candidate_id),
                "tali_passed": bool(tali_passed),
                "mainspring_passed": bool(ms_passed),
                "agreement": agreement,
                "tali_violations": len(tali_violations),
                "mainspring_violations": int(ms_violation_count),
                "scored_attrs": scored_attrs,
            },
        )
        if not agreement:
            # Emit a dedicated event too, so the disagreement can be alerted on
            # without parsing the compared event. Expected today: the two engines
            # use different fairness definitions + thresholds (see seam PARITY
            # NOTE) — this measures the gap the schema mapping must close.
            logger.info(
                "mainspring_bias_shadow status=disagreement candidate_id=%s "
                "tali_passed=%s mainspring_passed=%s",
                candidate_id, tali_passed, ms_passed,
                extra={
                    "event": "mainspring_bias_shadow",
                    "status": "disagreement",
                    "candidate_id": int(candidate_id),
                    "tali_passed": bool(tali_passed),
                    "mainspring_passed": bool(ms_passed),
                    "scored_attrs": scored_attrs,
                },
            )
    except Exception:  # pragma: no cover — shadow must never affect the live audit
        logger.exception("mainspring_bias_shadow: comparison failed (non-fatal)")
