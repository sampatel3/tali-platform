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
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy.orm import Session

from ..models.policy_version import PolicyVersion
from ..models.promotion_gate import BiasAuditResult
from .fitted_policy import FittedModel, apply_calibration, predict_proba_with_model


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
    if not target.exists():
        logger.warning("bias_audit_thresholds.yaml missing at %s; using defaults", target)
        return BiasThresholds()
    try:
        import yaml  # type: ignore[import-not-found]

        with target.open("r") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("failed to parse %s: %s — using defaults", target, exc)
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
    """
    if not examples:
        return ({}, [{"reason": "audit_set_empty", "severity": "blocker"}])

    # Predict once.
    preds = [predict_proba_with_model(model, ex.features) for ex in examples]
    labels = [ex.label for ex in examples]

    metrics: dict = {}
    violations: list[dict] = []

    for attr in thresholds.protected_attributes:
        # Group examples by segment value for this attribute.
        groups: dict[str, list[int]] = {}
        for i, ex in enumerate(examples):
            seg = ex.segments.get(attr)
            if seg is None:
                continue
            groups.setdefault(seg, []).append(i)
        if len(groups) < 2:
            # Can't measure parity without at least 2 segments. Record
            # the gap and move on — not a blocking violation.
            metrics[attr] = {"status": "insufficient_segments", "segments": list(groups.keys())}
            continue

        seg_summary: dict[str, dict] = {}
        for seg, idxs in groups.items():
            seg_preds = [preds[i] for i in idxs]
            seg_labels = [labels[i] for i in idxs]
            seg_summary[seg] = {
                "n": len(idxs),
                "selection_rate": _selection_rate(seg_preds),
                "hire_rate": _hire_rate(seg_labels),
                "ece": _ece(seg_preds, seg_labels),
            }
        metrics[attr] = seg_summary

        # Pairwise comparisons.
        seg_names = list(groups.keys())
        for i, a in enumerate(seg_names):
            for b in seg_names[i + 1 :]:
                ra = seg_summary[a]["selection_rate"] or 1e-9
                rb = seg_summary[b]["selection_rate"] or 1e-9
                dir_ratio = min(ra, rb) / max(ra, rb)
                if dir_ratio < thresholds.disparate_impact_ratio_min:
                    violations.append({
                        "attr": attr,
                        "kind": "disparate_impact",
                        "segments": [a, b],
                        "observed": dir_ratio,
                        "threshold": thresholds.disparate_impact_ratio_min,
                    })
                sel_gap = abs(ra - rb)
                if sel_gap > thresholds.selection_rate_parity_max_gap:
                    violations.append({
                        "attr": attr,
                        "kind": "selection_rate_gap",
                        "segments": [a, b],
                        "observed": sel_gap,
                        "threshold": thresholds.selection_rate_parity_max_gap,
                    })
                hire_gap = abs(seg_summary[a]["hire_rate"] - seg_summary[b]["hire_rate"])
                if hire_gap > thresholds.outcome_parity_max_gap:
                    violations.append({
                        "attr": attr,
                        "kind": "outcome_gap",
                        "segments": [a, b],
                        "observed": hire_gap,
                        "threshold": thresholds.outcome_parity_max_gap,
                    })
                ece_gap = abs(seg_summary[a]["ece"] - seg_summary[b]["ece"])
                if ece_gap > thresholds.calibration_parity_max_gap:
                    violations.append({
                        "attr": attr,
                        "kind": "calibration_gap",
                        "segments": [a, b],
                        "observed": ece_gap,
                        "threshold": thresholds.calibration_parity_max_gap,
                    })

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
