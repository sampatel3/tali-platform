"""Bias-audit seam — the brand-agnostic, ORM-free fairness verdict surface.

This is the convergence seam (ADR-0010, cut #4): the minimal, dependency-light
contract a brand (e.g. tali-platform) imports to evaluate the *fairness verdict*
(``metrics`` + ``violations``) a candidate model produces on per-group metrics —
WITHOUT pulling in mainspring's ``Case``/``PolicyVersion``/``Session``/ORM
machinery. Cut #4's cutover: the brand keeps computing the per-group metrics
(case loading, prediction, selection/outcome/calibration rates) but DELEGATES the
verdict to :func:`pairwise_fairness_verdict` here, so substrate and brand compute
the IDENTICAL fairness verdict.

The verdict is the EEOC 80%-rule (4/5ths) PAIRWISE disparate-impact test plus the
selection-rate / outcome / calibration (ECE) parity gaps — the compliance-signed-
off rule. It was contributed UP into the substrate (per ADR-0002) and now lives
in ``mainspring/governance/bias_audit.py:pairwise_fairness_verdict``; this seam is
the ORM-free lift of exactly that pure function (the governance module is itself
ORM-free, so the seam mirrors it byte-for-byte).

Per protected attribute, group the audit examples by segment, then for every
unordered pair of segments flag:
  * ``disparate_impact``  — ``min(rA,rB)/max(rA,rB) < disparate_impact_ratio_min``
    (4/5ths; default 0.80)
  * ``selection_rate_gap`` — ``|rA - rB| > selection_rate_parity_max_gap`` (0.05)
  * ``outcome_gap``       — ``|hireA - hireB| > outcome_parity_max_gap`` (0.07)
  * ``calibration_gap``   — ``|eceA - eceB| > calibration_parity_max_gap`` (0.05)
An attribute with < 2 segments is recorded ``insufficient_segments`` (no
violation). The candidate passes iff the violation list is empty.

Importable standalone: depends only on the stdlib, so a consumer can vendor
``seam.py`` and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable


AUDIT_GROUP_FIELD = "_audit_group"

# --- Fairness thresholds (EEOC 4/5ths + parity gaps). COMPLIANCE-SIGNED-OFF. --
# Mirror mainspring/governance/bias_audit.py and the brand's
# config/bias_audit_thresholds.yaml exactly — changing any is a compliance event.
DISPARATE_IMPACT_RATIO_MIN = 0.80    # 4/5ths rule: DIR below this is a violation
SELECTION_RATE_PARITY_MAX_GAP = 0.05  # |selection_rate_A - selection_rate_B|
OUTCOME_PARITY_MAX_GAP = 0.07         # |hire_rate_A - hire_rate_B|
CALIBRATION_PARITY_MAX_GAP = 0.05     # |ECE_A - ECE_B|

PROTECTED_ATTRIBUTES: tuple[str, ...] = (
    "gender",
    "race",
    "age_band",
    "nationality",
    "disability_status",
    "religion",
)

# Legacy demographic-parity constants — retained for back-compat imports; they no
# longer drive the verdict (the 4/5ths pairwise rule does).
MAX_PARITY_GAP = 0.15
MAX_ODDS_GAP = 0.20
MIN_GROUP_N = 5


@dataclass(frozen=True)
class BiasThresholds:
    """The pairwise-4/5ths fairness thresholds (mirrors mainspring governance +
    the brand's ``config/bias_audit_thresholds.yaml``)."""

    disparate_impact_ratio_min: float = DISPARATE_IMPACT_RATIO_MIN
    selection_rate_parity_max_gap: float = SELECTION_RATE_PARITY_MAX_GAP
    outcome_parity_max_gap: float = OUTCOME_PARITY_MAX_GAP
    calibration_parity_max_gap: float = CALIBRATION_PARITY_MAX_GAP
    protected_attributes: tuple[str, ...] = PROTECTED_ATTRIBUTES


@dataclass(frozen=True)
class SegmentMetrics:
    """One segment's already-computed metrics for one protected attribute — the
    ORM-free input the brand feeds in (it computed these in its own audit)."""

    segment: str
    n: int
    selection_rate: float
    hire_rate: float = 0.0
    ece: float = 0.0


def pairwise_fairness_verdict(
    *,
    metrics_by_attr: Mapping[str, Sequence[SegmentMetrics]],
    thresholds: BiasThresholds | None = None,
    protected_attributes: Sequence[str] | None = None,
) -> tuple[dict, list[dict]]:
    """The EEOC 4/5ths PAIRWISE verdict over pre-computed per-group metrics.

    ``metrics_by_attr`` maps each protected attribute to its segments'
    :class:`SegmentMetrics`. Returns ``(metrics_json, violations)`` in the
    brand's exact shape:

    * ``metrics_json[attr]`` is ``{seg: {n, selection_rate, hire_rate, ece}}``
      for a measurable attribute, or
      ``{"status": "insufficient_segments", "segments": [...]}`` when < 2 segments.
    * each violation dict is ``{attr, kind, segments: [a, b], observed, threshold}``
      with ``kind`` in {``disparate_impact``, ``selection_rate_gap``,
      ``outcome_gap``, ``calibration_gap``}.

    Deterministic iteration: attributes in ``protected_attributes`` order, then
    segments in metric-list order, then ordered pairs ``(i, j>i)``, then the four
    checks in DI / selection / outcome / calibration order.
    """
    thr = thresholds or BiasThresholds()
    attrs = list(protected_attributes if protected_attributes is not None else thr.protected_attributes)

    metrics: dict = {}
    violations: list[dict] = []

    for attr in attrs:
        segs = list(metrics_by_attr.get(attr, ()))
        if len(segs) < 2:
            metrics[attr] = {
                "status": "insufficient_segments",
                "segments": [s.segment for s in segs],
            }
            continue

        seg_summary: dict[str, dict] = {}
        for s in segs:
            seg_summary[s.segment] = {
                "n": s.n,
                "selection_rate": s.selection_rate,
                "hire_rate": s.hire_rate,
                "ece": s.ece,
            }
        metrics[attr] = seg_summary

        seg_names = [s.segment for s in segs]
        for i, a in enumerate(seg_names):
            for b in seg_names[i + 1:]:
                ra = seg_summary[a]["selection_rate"] or 1e-9
                rb = seg_summary[b]["selection_rate"] or 1e-9
                dir_ratio = min(ra, rb) / max(ra, rb)
                if dir_ratio < thr.disparate_impact_ratio_min:
                    violations.append({
                        "attr": attr,
                        "kind": "disparate_impact",
                        "segments": [a, b],
                        "observed": dir_ratio,
                        "threshold": thr.disparate_impact_ratio_min,
                    })
                sel_gap = abs(ra - rb)
                if sel_gap > thr.selection_rate_parity_max_gap:
                    violations.append({
                        "attr": attr,
                        "kind": "selection_rate_gap",
                        "segments": [a, b],
                        "observed": sel_gap,
                        "threshold": thr.selection_rate_parity_max_gap,
                    })
                hire_gap = abs(seg_summary[a]["hire_rate"] - seg_summary[b]["hire_rate"])
                if hire_gap > thr.outcome_parity_max_gap:
                    violations.append({
                        "attr": attr,
                        "kind": "outcome_gap",
                        "segments": [a, b],
                        "observed": hire_gap,
                        "threshold": thr.outcome_parity_max_gap,
                    })
                ece_gap = abs(seg_summary[a]["ece"] - seg_summary[b]["ece"])
                if ece_gap > thr.calibration_parity_max_gap:
                    violations.append({
                        "attr": attr,
                        "kind": "calibration_gap",
                        "segments": [a, b],
                        "observed": ece_gap,
                        "threshold": thr.calibration_parity_max_gap,
                    })

    return metrics, violations


@runtime_checkable
class BiasAuditor(Protocol):
    """The convergence contract: a pairwise-4/5ths fairness verdict over
    pre-computed per-group metrics, independent of how the brand loaded /
    predicted the cases."""

    def pairwise_fairness_verdict(
        self,
        *,
        metrics_by_attr: Mapping[str, Sequence[SegmentMetrics]],
        thresholds: Optional[BiasThresholds] = None,
        protected_attributes: Optional[Sequence[str]] = None,
    ) -> tuple[dict, list[dict]]:
        ...


def segment_metrics_from_summary(
    seg_summary: Mapping[str, Mapping[str, Any]],
) -> list[SegmentMetrics]:
    """Convenience: build a :class:`SegmentMetrics` list from one attribute's
    ``{seg: {n, selection_rate, hire_rate, ece}}`` block (the brand's metric
    shape), skipping non-segment markers (``status`` / ``segments``)."""
    out: list[SegmentMetrics] = []
    for seg, summ in seg_summary.items():
        if not isinstance(summ, Mapping) or "selection_rate" not in summ:
            continue
        out.append(SegmentMetrics(
            segment=str(seg),
            n=int(summ.get("n", 0)),
            selection_rate=float(summ.get("selection_rate", 0.0)),
            hire_rate=float(summ.get("hire_rate", 0.0)),
            ece=float(summ.get("ece", 0.0)),
        ))
    return out


__all__ = [
    "AUDIT_GROUP_FIELD",
    "DISPARATE_IMPACT_RATIO_MIN",
    "SELECTION_RATE_PARITY_MAX_GAP",
    "OUTCOME_PARITY_MAX_GAP",
    "CALIBRATION_PARITY_MAX_GAP",
    "PROTECTED_ATTRIBUTES",
    "MAX_PARITY_GAP",
    "MAX_ODDS_GAP",
    "MIN_GROUP_N",
    "BiasThresholds",
    "SegmentMetrics",
    "pairwise_fairness_verdict",
    "BiasAuditor",
    "segment_metrics_from_summary",
]
