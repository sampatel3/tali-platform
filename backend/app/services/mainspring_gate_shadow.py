"""Shadow comparator for the promotion-gate convergence (ADR-0010, cut #3).

Behind a flag (``MAINSPRING_GATE_SHADOW``), every promotion-gate run is ALSO
evaluated through mainspring's vendored gate-decision seam, and a gate-decision
agreement diff vs tali's own ``GateResult`` is logged. No DB writes, no behaviour
change — this is the at-parity evidence ADR-0010 requires *before* any gate
cutover. The vendored seam lives under ``backend/vendor/mainspring_gate``
(mirror-vendored from mainspring master; re-vendor via
``scripts/vendor_mainspring_gate.sh``).

What it compares: tali's three sub-checks (gold / bias / shadow) mapped onto
mainspring's three (holdout / bias / shadow) for the same policy-version
metrics, run through mainspring's pure ``evaluate_gate`` composition, and the
resulting gate **pass/fail** diffed against tali's ``GateResult.promoted``-vs-
all-passed.

Two outcomes are logged distinctly so the agreement log is actionable:
- ``compared``      — both gates produced a verdict; ``agree`` is True/False
- ``disagreement``  — the gates reached opposite pass/fail on the same metrics →
  a parity gap to investigate (schema-translation mismatch or a genuine rule
  divergence), surfaced as its own status (mirrors metering's ``unpriced``)
- the comparison never raises — a shadow failure must not affect the gate run.
"""
from __future__ import annotations

import logging
from typing import Sequence

from ..platform.config import settings

logger = logging.getLogger("taali.gate.shadow")


def shadow_compare_gate(
    *,
    policy_version_id: object,
    tali_passed: bool,
    gold_passed: bool,
    bias_passed: bool,
    shadow_passed: bool,
    auto_apply: bool = False,
    tali_reasons: Sequence[str] | None = None,
) -> None:
    """If gate shadow is on, evaluate the same sub-check metrics through
    mainspring's vendored gate-decision seam and log whether the two gates agree
    on pass/fail. Never raises.

    Tali's gate composes ``gold ∧ bias ∧ shadow``; mainspring's composes
    ``holdout ∧ bias ∧ shadow``. Tali's *gold* eval (candidate-vs-live log-loss
    on a curated set) is the analogue of mainspring's *holdout* eval (same
    candidate-vs-incumbent log-loss idea), so we map ``gold → holdout`` for the
    comparison. ``tali_passed`` is tali's composite verdict (all three sub-checks
    green) which the shadow checks against mainspring's ``GateDecision.passed``.
    """
    if not getattr(settings, "MAINSPRING_GATE_SHADOW", False):
        return
    try:
        from vendor.mainspring_gate.seam import SubCheck, evaluate_gate

        # Map tali's sub-checks onto mainspring's seam. gold ≈ holdout (both are
        # candidate-vs-incumbent log-loss within tolerance); bias and shadow map
        # one-to-one. Reasons aren't needed for the pass/fail verdict, so we pass
        # empty reason lists — the composition only reads ``passed``.
        decision = evaluate_gate(
            shadow=SubCheck(passed=bool(shadow_passed)),
            holdout=SubCheck(passed=bool(gold_passed)),
            bias=SubCheck(passed=bool(bias_passed)),
            auto_apply=bool(auto_apply),
        )

        tali_verdict = bool(tali_passed)
        ms_verdict = bool(decision.passed)
        agree = tali_verdict == ms_verdict

        if not agree:
            # The gates reached opposite pass/fail on the same metrics — a parity
            # gap (schema-translation mismatch or a genuine rule divergence), not
            # a benign event. Flag it as its own status so it stands out.
            logger.info(
                "mainspring_gate_shadow status=disagreement policy_version=%s "
                "tali_passed=%s mainspring_passed=%s mainspring_status=%s",
                policy_version_id, tali_verdict, ms_verdict, decision.status,
                extra={
                    "event": "mainspring_gate_shadow",
                    "status": "disagreement",
                    "policy_version_id": str(policy_version_id),
                    "tali_passed": tali_verdict,
                    "mainspring_passed": ms_verdict,
                    "mainspring_status": decision.status,
                    "mainspring_promoted": bool(decision.promoted),
                    "gold_passed": bool(gold_passed),
                    "bias_passed": bool(bias_passed),
                    "shadow_passed": bool(shadow_passed),
                    "auto_apply": bool(auto_apply),
                    "tali_reasons": list(tali_reasons or []),
                    "mainspring_reasons": list(decision.reasons),
                },
            )
            return

        logger.info(
            "mainspring_gate_shadow status=compared policy_version=%s agree=%s "
            "tali_passed=%s mainspring_passed=%s mainspring_status=%s",
            policy_version_id, agree, tali_verdict, ms_verdict, decision.status,
            extra={
                "event": "mainspring_gate_shadow",
                "status": "compared",
                "agree": agree,
                "policy_version_id": str(policy_version_id),
                "tali_passed": tali_verdict,
                "mainspring_passed": ms_verdict,
                "mainspring_status": decision.status,
                "mainspring_promoted": bool(decision.promoted),
                "gold_passed": bool(gold_passed),
                "bias_passed": bool(bias_passed),
                "shadow_passed": bool(shadow_passed),
                "auto_apply": bool(auto_apply),
            },
        )
    except Exception:  # pragma: no cover — shadow must never affect the gate run
        logger.exception("mainspring_gate_shadow: comparison failed (non-fatal)")
