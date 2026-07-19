"""Explicit, fail-closed fitted-policy promotion gate — Phase 5 §8.

Orchestrates the three checks (gold eval, bias audit, shadow mode)
before flipping a ``PolicyVersion`` from ``candidate`` → ``live``.

No production scheduler invokes :func:`run_gate`: the durable shadow decision
and realised-outcome lifecycle is still dormant. The callable is retained for
an explicit future rollout and defaults to human co-sign (no promotion). It can
only promote when a caller deliberately requests it and gold, bias, eligible
shadow-volume, and realised-outcome checks all pass.

Production currently calls only :func:`evaluate_auto_apply`, which is a
non-mutating safety check for rule-policy retune proposals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from ..models.policy_version import PolicyVersion
from ..models.promotion_gate import (
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
from .shadow_mode import is_eligible_for_conclusion
from vendor.mainspring_gate.seam import SubCheck, evaluate_gate


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
    auto_promote: bool = False,
) -> GateResult:
    """Run all three checks and flip ``candidate.status`` accordingly.

    Returns a ``GateResult`` describing what happened. ``auto_promote``
    controls whether the candidate is flipped to ``live`` immediately
    on a full pass. It defaults False: the gate writes the checks and
    leaves the candidate in ``shadow`` for human co-sign. There is no
    production caller that requests activation.
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
        outcomes_observed = int(summary.get("outcomes_observed") or 0)
        if not is_eligible_for_conclusion(latest, role_volume=role_volume):
            reasons.append(f"shadow_run_not_eligible: role_volume={role_volume}")
        elif outcomes_observed <= 0 or "candidate_accuracy_delta" not in summary:
            reasons.append("shadow_realised_outcomes_missing")
        elif disagreement_rate > SHADOW_DISAGREEMENT_CEILING:
            reasons.append(
                f"shadow_disagreement_too_high: {disagreement_rate:.2f} > "
                f"{SHADOW_DISAGREEMENT_CEILING:.2f}"
            )
        elif float(summary["candidate_accuracy_delta"]) < -0.1:
            # Candidate is materially worse than live on realised outcomes.
            reasons.append(
                "shadow_accuracy_regression: "
                f"{float(summary['candidate_accuracy_delta']):+.2f}"
            )
        else:
            shadow_passed = True

    # ADR-0010 cut #3 CUTOVER: the gate's AND-composition + promote decision is
    # sourced from mainspring's vendored gate-decision seam (``evaluate_gate``),
    # not hand-rolled here. The three sub-checks above (gold / bias / shadow) are
    # computed by tali exactly as before; only their composition moves to the
    # seam. Mapping into the seam's slots: gold → holdout (both are candidate-vs-
    # incumbent log-loss within tolerance), bias → bias, shadow → shadow. The seam
    # composes ``holdout ∧ bias ∧ shadow`` — identical to tali's prior
    # ``gold ∧ bias ∧ shadow``. ``decision.passed`` is the composite verdict and
    # ``decision.promoted`` is True iff passed-and-auto_apply; we drive tali's
    # existing status mutations + ``GateResult.promoted`` off those, so the
    # persisted ``PolicyVersion.status`` strings stay byte-identical. (The seam's
    # own status enum — active/gated/failed_gate — is mainspring's vocabulary and
    # is NOT written to tali rows; tali's status strings are live/rejected/
    # unchanged, unchanged by this cut.)
    decision = evaluate_gate(
        shadow=SubCheck(passed=shadow_passed),
        holdout=SubCheck(passed=gold_passed),
        bias=SubCheck(passed=bias_passed),
        auto_apply=auto_promote,
    )
    result = GateResult(
        promoted=decision.promoted,
        reasons=reasons,
        gold_passed=gold_passed,
        bias_passed=bias_passed,
        shadow_passed=shadow_passed,
    )

    if decision.promoted:
        # passed ∧ auto_promote → flip live. Archive the current live row first.
        if live is not None:
            live.status = "archived"
            live.archived_at = datetime.now(timezone.utc)
        candidate.status = "live"
        candidate.promoted_at = datetime.now(timezone.utc)
    elif not decision.passed:
        candidate.status = "rejected"
    else:
        # A full pass without an explicit activation request is a human-co-sign
        # state, never an implicit live flip.
        candidate.status = "shadow"

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
    - does NOT mutate any ``PolicyVersion.status`` (the automatic fitted-model
      promotion lifecycle is dormant; here we only read the candidate as a
      fail-closed safety signal for the separate rule-policy proposal).

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
