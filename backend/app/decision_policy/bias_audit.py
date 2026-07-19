"""Bias audit module — Phase 5 §8.2.

Inputs:
  - A ``PolicyVersion`` row whose ``model_json`` is the candidate model.
  - A held-out slice of historical decisions each tagged with a
    protected-attribute segment.
Outputs:
  - A ``BiasAuditResult`` row with per-segment metrics, violations, and
    pass/fail.

The audit is purely about *the candidate model's predictions on
historical data*, not about any live behaviour. Thresholds live in
``config/bias_audit_thresholds.yaml`` and require compliance sign-off
to change.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from sqlalchemy.orm import Session

from vendor.mainspring_bias.seam import (
    SegmentMetrics,
    BiasThresholds as SeamBiasThresholds,
    pairwise_fairness_verdict,
)

from ..models.policy_version import PolicyVersion
from ..models.promotion_gate import BiasAuditResult
from .fitted_policy import FittedModel, predict_proba_with_model


logger = logging.getLogger("taali.decision_policy.bias_audit")


CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "bias_audit_thresholds.yaml"


@dataclass
class AuditExample:
    """One held-out example with its protected-attribute segments."""

    features: dict[str, float]
    label: float  # 0/1 realised outcome
    segments: dict[str, str]  # {"gender": "F", "race": "white", ...}


@dataclass
class BiasThresholds:
    disparate_impact_ratio_min: float = 0.80
    calibration_parity_max_gap: float = 0.05
    selection_rate_parity_max_gap: float = 0.05
    outcome_parity_max_gap: float = 0.07
    protected_attributes: tuple[str, ...] = (
        "gender",
        "race",
        "age_band",
        "nationality",
        "disability_status",
        "religion",
    )
    allow_documented_override: bool = True


def load_thresholds(path: str | os.PathLike[str] | None = None) -> BiasThresholds:
    """Load thresholds from YAML. Defaults are the spec's starting values.

    YAML parsing falls back to the defaults if the file is missing or
    PyYAML isn't installed — pre-pilot envs may not have it. The
    promotion gate refuses to promote when thresholds can't be loaded
    and overrides aren't allowed.
    """
    target = Path(path) if path else CONFIG_PATH
    config_source = "custom" if path else "default"
    if not target.exists():
        logger.warning(
            "bias-audit thresholds missing; using defaults config_source=%s",
            config_source,
        )
        return BiasThresholds()
    try:
        import yaml  # type: ignore[import-not-found]

        with target.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning(
            "failed to parse bias-audit thresholds; using defaults "
            "config_source=%s error_type=%s",
            config_source,
            type(exc).__name__,
        )
        return BiasThresholds()
    return BiasThresholds(
        disparate_impact_ratio_min=float(raw.get("disparate_impact_ratio_min", 0.80)),
        calibration_parity_max_gap=float(raw.get("calibration_parity_max_gap", 0.05)),
        selection_rate_parity_max_gap=float(raw.get("selection_rate_parity_max_gap", 0.05)),
        outcome_parity_max_gap=float(raw.get("outcome_parity_max_gap", 0.07)),
        protected_attributes=tuple(raw.get("protected_attributes") or BiasThresholds.protected_attributes),
        allow_documented_override=bool(raw.get("allow_documented_override", True)),
    )


# ---------------------------------------------------------------------------
# Metric helpers (pure Python)
# ---------------------------------------------------------------------------


def _selection_rate(preds: Sequence[float], threshold: float = 0.5) -> float:
    if not preds:
        return 0.0
    return sum(1 for p in preds if p >= threshold) / len(preds)


def _hire_rate(labels: Sequence[float]) -> float:
    if not labels:
        return 0.0
    return sum(labels) / len(labels)


def _ece(preds: Sequence[float], labels: Sequence[float], *, bins: int = 10) -> float:
    if not preds:
        return 0.0
    bucketed: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for p, y in zip(preds, labels):
        idx = min(bins - 1, max(0, int(p * bins)))
        bucketed[idx].append((float(p), float(y)))
    total = 0.0
    for bucket in bucketed:
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_y = sum(y for _, y in bucket) / len(bucket)
        total += (len(bucket) / len(preds)) * abs(avg_p - avg_y)
    return total


# ---------------------------------------------------------------------------
# Audit core
# ---------------------------------------------------------------------------


def audit(
    *,
    model: FittedModel,
    examples: Sequence[AuditExample],
    thresholds: BiasThresholds,
) -> tuple[dict, list[dict]]:
    """Compute per-segment metrics and detect violations.

    Returns ``(metrics_json, violations_list)``. ``violations_list``
    is empty iff the audit passes.

    ADR-0010 cut #4 CUTOVER: tali still computes the per-segment metrics
    (selection / outcome / calibration rates) here, but the fairness VERDICT
    (the EEOC 4/5ths pairwise disparate-impact test + the three parity gaps) is
    delegated to mainspring's vendored bias seam
    (:func:`pairwise_fairness_verdict`) — the same pure rule, now owned by the
    substrate. The verdict is byte-identical to tali's prior inline logic (locked
    by ``test_bias_seam_parity.py``); only its location moves.
    """
    if not examples:
        return ({}, [{"reason": "audit_set_empty", "severity": "blocker"}])

    # Predict once.
    preds = [predict_proba_with_model(model, ex.features) for ex in examples]
    labels = [ex.label for ex in examples]

    # Compute per-attribute, per-segment metrics (UNCHANGED). The verdict over
    # these metrics is then rendered by the vendored seam.
    metrics_by_attr: dict[str, list[SegmentMetrics]] = {}
    for attr in thresholds.protected_attributes:
        # Group examples by segment value for this attribute.
        groups: dict[str, list[int]] = {}
        for i, ex in enumerate(examples):
            seg = ex.segments.get(attr)
            if seg is None:
                continue
            groups.setdefault(seg, []).append(i)
        seg_list: list[SegmentMetrics] = []
        for seg, idxs in groups.items():
            seg_preds = [preds[i] for i in idxs]
            seg_labels = [labels[i] for i in idxs]
            seg_list.append(SegmentMetrics(
                segment=seg,
                n=len(idxs),
                selection_rate=_selection_rate(seg_preds),
                hire_rate=_hire_rate(seg_labels),
                ece=_ece(seg_preds, seg_labels),
            ))
        metrics_by_attr[attr] = seg_list

    seam_thresholds = SeamBiasThresholds(
        disparate_impact_ratio_min=thresholds.disparate_impact_ratio_min,
        selection_rate_parity_max_gap=thresholds.selection_rate_parity_max_gap,
        outcome_parity_max_gap=thresholds.outcome_parity_max_gap,
        calibration_parity_max_gap=thresholds.calibration_parity_max_gap,
        protected_attributes=tuple(thresholds.protected_attributes),
    )
    metrics, violations = pairwise_fairness_verdict(
        metrics_by_attr=metrics_by_attr,
        thresholds=seam_thresholds,
        protected_attributes=list(thresholds.protected_attributes),
    )
    return metrics, violations


def write_audit_result(
    db: Session,
    *,
    policy_version: PolicyVersion,
    model: FittedModel,
    examples: Sequence[AuditExample],
    thresholds: BiasThresholds | None = None,
) -> BiasAuditResult:
    thr = thresholds or load_thresholds()
    # ADR-0010 cut #4 CUTOVER: the verdict is now mainspring's vendored bias seam
    # (delegated inside ``audit`` via ``pairwise_fairness_verdict``). The shadow
    # comparator + ``MAINSPRING_BIAS_SHADOW`` flag were removed once parity was
    # proven (see ``test_bias_seam_parity.py``); the substrate now IS the verdict.
    metrics, violations = audit(model=model, examples=examples, thresholds=thr)
    row = BiasAuditResult(
        policy_version_id=int(policy_version.id),
        metrics_json=metrics,
        violations_json=violations,
        passed=len(violations) == 0,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "AuditExample",
    "BiasThresholds",
    "audit",
    "load_thresholds",
    "write_audit_result",
]
