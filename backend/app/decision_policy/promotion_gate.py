"""Promotion gate — Phase 5 §8.

Orchestrates the three checks (gold eval, bias audit, shadow mode)
before flipping a ``PolicyVersion`` from ``candidate`` → ``live``.

The gate runs at the end of the nightly cycle (after shadow runs
conclude). Each check produces a structured pass/fail; the gate
records the outcome on each row and only promotes when all three
pass (or an explicit override has been filed for bias).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from ..models.policy_version import PolicyVersion
from ..models.promotion_gate import (
    BiasAuditResult,
    GoldEvalExample,
    ShadowRun,
)
from .bias_audit import (
    AuditExample,
    BiasThresholds,
    audit,
    load_thresholds,
    write_audit_result,
)
from .fitted_policy import FittedModel, predict_proba_with_model
from .shadow_mode import conclude_shadow_run, is_eligible_for_conclusion


logger = logging.getLogger("taali.decision_policy.promotion_gate")


# Tolerance vs the live policy on the gold eval set. The candidate must
# be at least this close to (or better than) the live policy's log-loss.
GOLD_EVAL_LOG_LOSS_TOLERANCE = 0.05
# Disagreement-rate ceiling — too-divergent shadow runs are suspicious.
SHADOW_DISAGREEMENT_CEILING = 0.40


@dataclass
class GateResult:
    promoted: bool
    reasons: list[str]
    gold_passed: bool = False
    bias_passed: bool = False
    shadow_passed: bool = False


@dataclass
class AutoApplyGateResult:
    """Outcome of the synchronous auto-apply safety gate.

    ``passed`` is the only thing the caller must honour: auto-apply may
    flip the policy live iff ``passed`` is True. ``cold_start`` flags the
    "no data to judge yet" case (no fitted candidate, no gold set, or no
    audit holdout) so the caller can record it distinctly from an actual
    safety failure — both block, but for different reasons.
    """

    passed: bool
    cold_start: bool
    reasons: list[str]
    gold_passed: bool = False
    bias_passed: bool = False


# ---------------------------------------------------------------------------
# Gold eval check
# ---------------------------------------------------------------------------


def _log_loss(preds: Sequence[float], labels: Sequence[float]) -> float:
    import math
    if not preds:
        return 0.0
    total = 0.0
    for p, y in zip(preds, labels):
        p_c = max(min(p, 1 - 1e-9), 1e-9)
        total += -(y * math.log(p_c) + (1 - y) * math.log(1 - p_c))
    return total / len(preds)


def evaluate_gold_set(
    db: Session,
    *,
    candidate: FittedModel,
    live: FittedModel | None,
    organization_id: int,
    role_id: int | None,
) -> tuple[bool, dict]:
    """Compare candidate vs live log-loss on the gold eval set.

    Passes when candidate's log-loss is within tolerance of the live
    policy's. When no live policy exists yet, passes as long as the
    candidate's log-loss is finite.
    """
    examples = (
        db.query(GoldEvalExample)
        .filter(
            GoldEvalExample.organization_id == organization_id,
            (
                GoldEvalExample.role_id == role_id
                if role_id is not None
                else GoldEvalExample.role_id.is_(None)
            ),
        )
        .all()
    )
    if not examples:
        # No gold set seeded → cannot validate. Conservative: refuse
        # promotion until someone curates a set.
        return False, {"reason": "no_gold_eval_examples"}
    cand_preds = [
        predict_proba_with_model(candidate, ex.features_json or {})
        for ex in examples
    ]
    labels = [float(ex.expected_outcome) for ex in examples]
    cand_loss = _log_loss(cand_preds, labels)
    metrics: dict = {"candidate_log_loss": cand_loss, "n": len(examples)}
    if live is None:
        return True, metrics
    live_preds = [
        predict_proba_with_model(live, ex.features_json or {}) for ex in examples
    ]
    live_loss = _log_loss(live_preds, labels)
    metrics["live_log_loss"] = live_loss
    metrics["delta"] = cand_loss - live_loss
    # Candidate is at most ``tolerance`` worse than live.
    return metrics["delta"] <= GOLD_EVAL_LOG_LOSS_TOLERANCE, metrics


# ---------------------------------------------------------------------------
# Top-level gate
# ---------------------------------------------------------------------------


def run_gate(
    db: Session,
    *,
    candidate: PolicyVersion,
    live: PolicyVersion | None,
    audit_examples: Sequence[AuditExample],
    role_volume: str = "high",
    thresholds: BiasThresholds | None = None,
    auto_promote: bool = True,
) -> GateResult:
    """Run all three checks and flip ``candidate.status`` accordingly.

    Returns a ``GateResult`` describing what happened. ``auto_promote``
    controls whether the candidate is flipped to ``live`` immediately
    on a full pass. Setting it False makes the gate write the checks
    but leave the candidate in ``shadow`` for human co-sign.
    """
    reasons: list[str] = []

    candidate_model = FittedModel.from_dict(candidate.model_json or {})
    live_model = (
        FittedModel.from_dict(live.model_json or {}) if live is not None else None
    )

    # 1. Gold eval set.
    gold_passed, gold_metrics = evaluate_gold_set(
        db,
        candidate=candidate_model,
        live=live_model,
        organization_id=int(candidate.organization_id),
        role_id=int(candidate.role_id) if candidate.role_id else None,
    )
    if not gold_passed:
        reasons.append(f"gold_eval_failed: {gold_metrics}")

    # 2. Bias audit.
    audit_row = write_audit_result(
        db,
        policy_version=candidate,
        model=candidate_model,
        examples=audit_examples,
        thresholds=thresholds,
    )
    bias_passed = bool(audit_row.passed)
    if not bias_passed:
        reasons.append(
            f"bias_audit_failed: {len(audit_row.violations_json or [])} violations"
        )

    # 3. Shadow mode.
    shadow_passed = False
    shadow_rows = (
        db.query(ShadowRun)
        .filter(
            ShadowRun.candidate_policy_version_id == int(candidate.id),
            ShadowRun.status == "concluded",
        )
        .order_by(ShadowRun.ended_at.desc())
        .all()
    )
    if not shadow_rows:
        reasons.append("no_concluded_shadow_run")
    else:
        latest = shadow_rows[0]
        summary = (latest.metrics_json or {}).get("summary") or {}
        disagreement_rate = float(summary.get("disagreement_rate") or 0.0)
        delta = float(summary.get("candidate_accuracy_delta") or 0.0)
        if disagreement_rate > SHADOW_DISAGREEMENT_CEILING:
            reasons.append(
                f"shadow_disagreement_too_high: {disagreement_rate:.2f} > "
                f"{SHADOW_DISAGREEMENT_CEILING:.2f}"
            )
        elif delta < -0.1:
            # Candidate is materially worse than live on realised outcomes.
            reasons.append(f"shadow_accuracy_regression: {delta:+.2f}")
        else:
            shadow_passed = True

    all_pass = gold_passed and bias_passed and shadow_passed
    result = GateResult(
        promoted=False,
        reasons=reasons,
        gold_passed=gold_passed,
        bias_passed=bias_passed,
        shadow_passed=shadow_passed,
    )

    if all_pass and auto_promote:
        # Archive the current live row first.
        if live is not None:
            live.status = "archived"
            live.archived_at = datetime.now(timezone.utc)
        candidate.status = "live"
        candidate.promoted_at = datetime.now(timezone.utc)
        result.promoted = True
    elif not all_pass:
        candidate.status = "rejected"

    db.flush()
    return result


def evaluate_auto_apply(
    db: Session,
    *,
    candidate: PolicyVersion | None,
    live: PolicyVersion | None,
    audit_examples: Sequence[AuditExample],
    thresholds: BiasThresholds | None = None,
    require_gold: bool = True,
) -> AutoApplyGateResult:
    """Synchronous safety gate for the *auto-apply* retune path.

    Auto-apply removes the human approval click, not the safety checks.
    Unlike :func:`run_gate` this:

    - does NOT require a concluded shadow run (there isn't one at
      proposal time — shadow mode is a multi-day process), and
    - does NOT mutate any ``PolicyVersion.status`` (the fitted-model
      lifecycle is owned by the real promotion flow; here we only read
      the candidate to judge whether shipping a learned change is safe).

    It runs the two checks that *can* be evaluated synchronously against
    the org's latest fitted candidate model:

    1. Bias audit — **non-bypassable**. An empty audit holdout yields a
       blocker violation, so it fails closed.
    2. Gold-set log-loss check — enforced when ``require_gold`` (default).

    Cold start: when there is no fitted candidate model, no gold set, or
    no audit holdout, the gate refuses (``passed=False``,
    ``cold_start=True``). Auto-apply must never activate into a vacuum —
    the caller falls back to writing an inactive proposal for human
    review, exactly like the non-auto-apply path.
    """
    if candidate is None:
        return AutoApplyGateResult(
            passed=False,
            cold_start=True,
            reasons=["no_fitted_candidate_model"],
        )

    thr = thresholds or load_thresholds()
    candidate_model = FittedModel.from_dict(candidate.model_json or {})
    live_model = (
        FittedModel.from_dict(live.model_json or {}) if live is not None else None
    )

    reasons: list[str] = []

    # 1. Bias audit (non-bypassable). ``audit`` returns a blocker
    # violation for an empty example set, so this fails closed.
    _, violations = audit(
        model=candidate_model, examples=audit_examples, thresholds=thr
    )
    bias_passed = not violations
    if not bias_passed:
        reasons.append(f"bias_audit_failed: {len(violations)} violation(s)")

    # 2. Gold-set log-loss check.
    gold_passed, gold_metrics = evaluate_gold_set(
        db,
        candidate=candidate_model,
        live=live_model,
        organization_id=int(candidate.organization_id),
        role_id=int(candidate.role_id) if candidate.role_id else None,
    )
    if require_gold and not gold_passed:
        reasons.append(f"gold_eval_failed: {gold_metrics}")

    cold_start = (not audit_examples) or (
        require_gold and gold_metrics.get("reason") == "no_gold_eval_examples"
    )
    passed = bias_passed and (gold_passed or not require_gold)
    return AutoApplyGateResult(
        passed=passed,
        cold_start=cold_start,
        reasons=reasons,
        gold_passed=gold_passed,
        bias_passed=bias_passed,
    )


__all__ = [
    "AutoApplyGateResult",
    "GateResult",
    "GOLD_EVAL_LOG_LOSS_TOLERANCE",
    "SHADOW_DISAGREEMENT_CEILING",
    "evaluate_auto_apply",
    "evaluate_gold_set",
    "run_gate",
]
