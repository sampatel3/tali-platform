"""Fitted policy model — Phase 3 §6.3.

Replaces the heuristic threshold/weight retuner for the *composition*
step. The rule-driven engine (``decision_policy.engine``) still owns
how rules are walked; this module owns the calibrated probability
``P(positive_outcome | sub_agent_scores)`` that the engine consults.

Why pure-Python logistic regression (not LightGBM / sklearn):
- Pre-pilot decision volumes are O(100s/role), not O(millions).
  Logistic with hand-rolled gradient descent converges in ms.
- No new heavyweight deps to deploy. The model JSON is a flat dict
  of feature → coefficient.
- The architecture spec (D1) chose simplicity-first; a swap to
  LightGBM later only touches ``fit_model`` and ``predict_proba``.

The hierarchical pooling is hand-rolled per the spec's D1
recommendation: a ``sqrt(n)`` shrinkage of role-level residuals
toward the org-level baseline. Low-volume roles (n < 10 decisions)
inherit the org-level model directly.

Isotonic calibration is a final monotone-only adjustment over the
held-out predictions vs realised outcomes. Pure Python; O(n log n)
in the gold-set size.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from ..models.policy_version import PolicyVersion


logger = logging.getLogger("taali.decision_policy.fitted_policy")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class TrainingExample:
    """One row the fitter consumes.

    ``features`` is keyed by canonical feature name (pre_screen_score,
    cv_role_fit_score, assessment_quality, graph_prior_p_advance,
    etc.). ``label`` is 1.0 / 0.0 for hired / not. ``weight`` lets the
    fitter down-weight weak labels (recruiter approvals without
    realised outcomes).
    """

    features: dict[str, float]
    label: float
    weight: float = 1.0
    role_id: int | None = None


@dataclass
class FittedModel:
    """Serialisable model — what gets stored in ``policy_versions.model_json``."""

    coefs: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    # Org-level fallback when a role-level model is too thin to trust.
    org_coefs: dict[str, float] = field(default_factory=dict)
    org_intercept: float = 0.0
    role_sample_count: int = 0
    # Isotonic-calibration breakpoints: monotone-increasing list of
    # (raw_prob, calibrated_prob) pairs. predict_proba does
    # piecewise-linear interpolation. Empty = no calibration applied.
    calibration: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "coefs": dict(self.coefs),
            "intercept": float(self.intercept),
            "org_coefs": dict(self.org_coefs),
            "org_intercept": float(self.org_intercept),
            "role_sample_count": int(self.role_sample_count),
            "calibration": [(float(a), float(b)) for a, b in self.calibration],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FittedModel":
        return cls(
            coefs={k: float(v) for k, v in (d.get("coefs") or {}).items()},
            intercept=float(d.get("intercept") or 0.0),
            org_coefs={k: float(v) for k, v in (d.get("org_coefs") or {}).items()},
            org_intercept=float(d.get("org_intercept") or 0.0),
            role_sample_count=int(d.get("role_sample_count") or 0),
            calibration=[(float(a), float(b)) for a, b in (d.get("calibration") or [])],
        )


# ---------------------------------------------------------------------------
# Pure-Python logistic regression
# ---------------------------------------------------------------------------


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _fit_logistic(
    examples: Sequence[TrainingExample],
    feature_names: Sequence[str],
    *,
    max_iter: int = 200,
    learning_rate: float = 0.05,
    l2: float = 0.01,
    tol: float = 1e-5,
) -> tuple[dict[str, float], float]:
    """Fit a weighted L2-regularised logistic regression.

    Returns ``(coefs_dict, intercept)``. Gradient descent with a fixed
    step size — sufficient for the smooth, well-scaled feature vectors
    we generate (rescaling happens in ``canonical_features``).
    """
    coefs = {name: 0.0 for name in feature_names}
    intercept = 0.0
    if not examples:
        return coefs, intercept

    for it in range(max_iter):
        grad_w = {name: 0.0 for name in feature_names}
        grad_b = 0.0
        loss = 0.0
        total_w = 0.0
        for ex in examples:
            z = intercept + sum(coefs[n] * float(ex.features.get(n, 0.0)) for n in feature_names)
            p = _sigmoid(z)
            err = (p - ex.label) * ex.weight
            for n in feature_names:
                grad_w[n] += err * float(ex.features.get(n, 0.0))
            grad_b += err
            # Add to log-loss (for diagnostics / convergence check).
            p_clamped = max(min(p, 1 - 1e-9), 1e-9)
            loss += -(ex.label * math.log(p_clamped) + (1 - ex.label) * math.log(1 - p_clamped)) * ex.weight
            total_w += ex.weight
        # L2 (skip intercept).
        for n in feature_names:
            grad_w[n] += l2 * coefs[n]
        # Update.
        max_step = 0.0
        for n in feature_names:
            step = learning_rate * grad_w[n] / max(1.0, total_w)
            coefs[n] -= step
            max_step = max(max_step, abs(step))
        intercept -= learning_rate * grad_b / max(1.0, total_w)
        if max_step < tol:
            logger.debug("logistic converged at iter %d (loss=%.4f)", it, loss / max(1.0, total_w))
            return coefs, intercept

    logger.debug("logistic hit max_iter without sub-tol convergence")
    return coefs, intercept


# ---------------------------------------------------------------------------
# Hierarchical pooling: org baseline + role-level residual
# ---------------------------------------------------------------------------


def fit_pooled(
    examples: Sequence[TrainingExample],
    role_id: int | None,
    *,
    feature_names: Sequence[str] | None = None,
    pooling_floor: int = 10,
    pooling_saturation: int = 100,
    l2: float = 0.01,
    learning_rate: float = 0.05,
    max_iter: int = 200,
) -> FittedModel:
    """Fit org-level baseline + role-level residual with sqrt(n) shrinkage.

    Algorithm:
      1. Fit a model on the full ``examples`` (org-level).
      2. If ``role_id`` is None, return the org-level model.
      3. Filter to role examples. If fewer than ``pooling_floor``,
         return the org-level model verbatim (role inherits org).
      4. Otherwise fit a role-level model on the role examples and
         blend with the org-level via ``alpha = min(1, sqrt(n/sat))``.
         alpha=1 means full role-level use; alpha<1 shrinks toward org.

    The ``l2`` / ``learning_rate`` / ``max_iter`` defaults reproduce the
    historical hand-tuned fit exactly; they are exposed so the
    autoresearch loop can search over them without changing the
    production default.
    """
    if feature_names is None:
        feature_names = sorted(
            {k for ex in examples for k in ex.features.keys()}
        )
    feature_names = list(feature_names)

    fit_kwargs = {"l2": l2, "learning_rate": learning_rate, "max_iter": max_iter}
    org_coefs, org_intercept = _fit_logistic(examples, feature_names, **fit_kwargs)
    if role_id is None or not examples:
        return FittedModel(
            coefs=org_coefs,
            intercept=org_intercept,
            org_coefs=org_coefs,
            org_intercept=org_intercept,
            role_sample_count=len(examples),
        )

    role_examples = [ex for ex in examples if ex.role_id == role_id]
    n = len(role_examples)
    if n < pooling_floor:
        return FittedModel(
            coefs=org_coefs,
            intercept=org_intercept,
            org_coefs=org_coefs,
            org_intercept=org_intercept,
            role_sample_count=n,
        )

    role_coefs, role_intercept = _fit_logistic(role_examples, feature_names, **fit_kwargs)
    alpha = min(1.0, math.sqrt(n / float(pooling_saturation)))
    blended = {
        name: alpha * role_coefs[name] + (1.0 - alpha) * org_coefs[name]
        for name in feature_names
    }
    blended_intercept = alpha * role_intercept + (1.0 - alpha) * org_intercept
    return FittedModel(
        coefs=blended,
        intercept=blended_intercept,
        org_coefs=org_coefs,
        org_intercept=org_intercept,
        role_sample_count=n,
    )


# ---------------------------------------------------------------------------
# Isotonic calibration — monotone piecewise-linear map raw → calibrated
# ---------------------------------------------------------------------------


def isotonic_calibration(
    raw_preds: Sequence[float], labels: Sequence[float]
) -> list[tuple[float, float]]:
    """Pool-Adjacent-Violators isotonic regression.

    Returns a list of (x, y) breakpoints where x is monotonically
    increasing. ``apply_calibration`` does piecewise-linear interpolation.

    Pure-Python PAV is O(n) amortised; pre-pilot gold sets are O(100s)
    so the constant factor doesn't matter.
    """
    n = len(raw_preds)
    if n == 0:
        return []
    pairs = sorted(zip(raw_preds, labels), key=lambda p: p[0])
    blocks: list[list[float]] = [[float(p[0]), float(p[1]), 1.0] for p in pairs]
    # Each block: [x_mean, y_mean, weight].
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][1] > blocks[i + 1][1]:
            # Violator — merge.
            x = (
                blocks[i][0] * blocks[i][2] + blocks[i + 1][0] * blocks[i + 1][2]
            ) / (blocks[i][2] + blocks[i + 1][2])
            y = (
                blocks[i][1] * blocks[i][2] + blocks[i + 1][1] * blocks[i + 1][2]
            ) / (blocks[i][2] + blocks[i + 1][2])
            blocks[i] = [x, y, blocks[i][2] + blocks[i + 1][2]]
            del blocks[i + 1]
            # Walk back to check earlier blocks now violate.
            while i > 0 and blocks[i - 1][1] > blocks[i][1]:
                i -= 1
        else:
            i += 1
    return [(b[0], b[1]) for b in blocks]


def apply_calibration(raw: float, breakpoints: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation across PAV blocks."""
    if not breakpoints:
        return raw
    if raw <= breakpoints[0][0]:
        return breakpoints[0][1]
    if raw >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    # Find bracketing breakpoints.
    for (x0, y0), (x1, y1) in zip(breakpoints, breakpoints[1:]):
        if x0 <= raw <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
    return raw


# ---------------------------------------------------------------------------
# Top-level fit + predict
# ---------------------------------------------------------------------------


def fit_model(
    examples: Sequence[TrainingExample],
    *,
    role_id: int | None,
    gold_set: Sequence[TrainingExample] | None = None,
    l2: float = 0.01,
    learning_rate: float = 0.05,
    max_iter: int = 200,
    pooling_saturation: int = 100,
    calibrate: bool = True,
) -> tuple[FittedModel, dict]:
    """Fit + calibrate. Returns ``(model, metrics_dict)``.

    Metrics include training log-loss, hold-out log-loss (when gold_set
    given), and calibration ECE (expected calibration error) so the
    Phase 5 promotion gate can read them off the row.

    The hyperparameter arguments default to the historical production
    values (zero behaviour change); the autoresearch loop overrides them
    to search for a better-calibrated fit under the bias constraint.
    """
    model = fit_pooled(
        examples,
        role_id=role_id,
        pooling_saturation=pooling_saturation,
        l2=l2,
        learning_rate=learning_rate,
        max_iter=max_iter,
    )

    metrics: dict = {
        "training_examples": len(examples),
        "role_examples": model.role_sample_count,
    }

    if gold_set:
        # Train-time predictions on the gold set, pre-calibration.
        raw_preds = [
            predict_proba_with_model(model, ex.features, calibrated=False)
            for ex in gold_set
        ]
        labels = [ex.label for ex in gold_set]
        if calibrate:
            model.calibration = isotonic_calibration(raw_preds, labels)
        # Holdout metrics on whatever map (calibrated or identity) is active.
        scored = [apply_calibration(p, model.calibration) for p in raw_preds]
        metrics["holdout_log_loss"] = _log_loss(scored, labels)
        metrics["holdout_ece"] = _ece(scored, labels, bins=10)

    return model, metrics


def predict_proba_with_model(
    model: FittedModel, features: dict[str, float], *, calibrated: bool = True
) -> float:
    z = model.intercept + sum(
        model.coefs.get(k, 0.0) * float(features.get(k, 0.0))
        for k in model.coefs
    )
    p = _sigmoid(z)
    if calibrated:
        return apply_calibration(p, model.calibration)
    return p


def _log_loss(preds: Sequence[float], labels: Sequence[float]) -> float:
    if not preds:
        return 0.0
    total = 0.0
    for p, y in zip(preds, labels):
        p_c = max(min(p, 1 - 1e-9), 1e-9)
        total += -(y * math.log(p_c) + (1 - y) * math.log(1 - p_c))
    return total / len(preds)


def _ece(preds: Sequence[float], labels: Sequence[float], *, bins: int = 10) -> float:
    """Expected Calibration Error — average gap between bin confidence and bin accuracy."""
    if not preds:
        return 0.0
    bucketed: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for p, y in zip(preds, labels):
        idx = min(bins - 1, max(0, int(p * bins)))
        bucketed[idx].append((float(p), float(y)))
    total = 0.0
    n = len(preds)
    for bucket in bucketed:
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_y = sum(y for _, y in bucket) / len(bucket)
        total += (len(bucket) / n) * abs(avg_p - avg_y)
    return total


# ---------------------------------------------------------------------------
# Active-model lookup (used by the policy engine at evaluate time)
# ---------------------------------------------------------------------------


def load_live_model(
    db: Session, *, organization_id: int, role_id: int | None
) -> FittedModel | None:
    """Return the active fitted model for (org, role), or None.

    Resolution:
      1. Role-specific live row.
      2. Org-default live row (role_id IS NULL).
    Returns None when nothing has been promoted yet — the engine then
    skips the fitted-policy composition step and the rule-driven path
    remains the only producer of decisions.
    """
    if role_id is not None:
        row = (
            db.query(PolicyVersion)
            .filter(
                PolicyVersion.organization_id == organization_id,
                PolicyVersion.role_id == role_id,
                PolicyVersion.status == "live",
            )
            .order_by(PolicyVersion.promoted_at.desc())
            .first()
        )
        if row:
            return FittedModel.from_dict(row.model_json)
    row = (
        db.query(PolicyVersion)
        .filter(
            PolicyVersion.organization_id == organization_id,
            PolicyVersion.role_id.is_(None),
            PolicyVersion.status == "live",
        )
        .order_by(PolicyVersion.promoted_at.desc())
        .first()
    )
    if row:
        return FittedModel.from_dict(row.model_json)
    return None


__all__ = [
    "FittedModel",
    "TrainingExample",
    "apply_calibration",
    "fit_model",
    "fit_pooled",
    "isotonic_calibration",
    "load_live_model",
    "predict_proba_with_model",
]
