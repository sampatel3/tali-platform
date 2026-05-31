"""Shadow comparator for the decision-policy convergence (ADR-0010, cut #2).

Behind a flag (``MAINSPRING_POLICY_SHADOW``), every deterministic policy
verdict tali emits is ALSO re-derived through mainspring's vendored
``PolicyEngine`` — a ``DomainSpec``-shaped translation of the same
``DecisionPolicyRow`` — and a *verdict agreement/disagreement* diff is logged.
No DB writes, no behaviour change: this is the at-parity evidence ADR-0010
requires *before* any cutover to the substrate engine. The vendored engine
lives under ``backend/vendor/mainspring_policy`` (mirror-vendored from
mainspring master; re-vendor via ``scripts/vendor_mainspring_policy.sh``).

The hard part is the DomainSpec translation. tali's policy is a
``PolicyJson`` of named decision points, each carrying string-expression
rules (``if`` parsed by ``engine._eval_condition``), weighted thresholds, and
a confidence floor; mainspring's ``PolicyEngine`` is a flat list of
``Rule(when=callable, then, priority)`` over a single signal context. We do a
**best-effort field map**: flatten tali's decision points into mainspring
rules in tali's fixed point order (send_assessment > advance > reject), reuse
tali's *pure* expression evaluator as each rule's ``when`` predicate (so we
compare engine control-flow/priority semantics, not re-implement the rule
language), and build a ``SignalBundle`` + flags from the same
``DecisionInputs``. Pieces that don't translate (graph priors, intent
overlay, the weighted-score fallthrough) are recorded as a ``gap`` status
rather than failing.

Four outcomes are logged distinctly so the parity log is actionable:
- ``agree``       — both engines emit the same ``decision_type`` → at parity
- ``disagree``    — engines emit different ``decision_type`` → a gap to close
- ``gap``         — the row carried rules/structure we couldn't translate, so
  the comparison is inconclusive (a translation TODO, not a real disagreement)
- the comparison never raises — a shadow failure must not affect the verdict.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..platform.config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cost
    from ..decision_policy.engine import DecisionInputs, PolicyDecision
    from ..decision_policy.schema import PolicyJson

logger = logging.getLogger("taali.policy.shadow")


# tali decision-point order is fixed (send before advance before reject); the
# mainspring engine has no notion of points, so we flatten the points into one
# rule list and bias priority by point order to preserve "first point wins".
_POINT_ORDER = ("send_assessment", "advance_to_interview", "reject")
# Within mainspring's single rule list, a higher base keeps an earlier point's
# rules strictly above a later point's, mirroring tali's point cascade.
_POINT_PRIORITY_BASE = {"send_assessment": 30_000, "advance_to_interview": 20_000, "reject": 10_000}

# tali rule verbs that short-circuit a point without queueing; mainspring
# treats ``skip``/``no_action`` the same way, so they map straight through.
_PASSIVE_VERBS = {"skip", "no_action"}


def _build_signal_bundle(inputs: "DecisionInputs", SignalBundle: Any, Signal: Any) -> Any:
    """Translate tali's score-like inputs into a mainspring ``SignalBundle``.

    tali scores (role_fit_score, pre_screen_score, …) become signals at full
    confidence; graph priors are lifted 0..1 → 0..100 and carry their reported
    confidence. The names are kept verbatim so the rule context matches the
    one tali's evaluator sees.
    """
    bundle = SignalBundle()
    for name, value in (inputs.scores or {}).items():
        try:
            bundle.add(Signal(name=name, value=float(value), confidence=1.0))
        except (TypeError, ValueError):
            continue
    prior_conf = float((inputs.graph_priors or {}).get("confidence", 0.0) or 0.0)
    for name, value in (inputs.graph_priors or {}).items():
        if name == "confidence" or value is None:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 <= v <= 1.0:
            v = v * 100.0
        bundle.add(Signal(name=f"graph_prior_{name}", value=v, confidence=prior_conf))
    return bundle


def _translate_to_engine(
    policy: "PolicyJson", inputs: "DecisionInputs", PolicyEngine: Any, Rule: Any
) -> tuple[Any, list[str]]:
    """Best-effort DomainSpec translation: tali ``PolicyJson`` → mainspring
    ``PolicyEngine``. Returns ``(engine, untranslatable)`` where the second
    list names structures we could not faithfully map (logged as a gap)."""
    from ..decision_policy.engine import _build_rule_context, _eval_condition

    rules: list[Any] = []
    thresholds: dict[str, float] = {}
    untranslatable: list[str] = []

    for point_name in _POINT_ORDER:
        point = policy.decision_points.get(point_name)
        if point is None:
            continue
        # The rule context tali evaluates against is per-point (it folds in the
        # point's thresholds). Capture it once and reuse it as the predicate's
        # closure so mainspring's ``when`` sees exactly tali's view.
        try:
            point_ctx = _build_rule_context(inputs, point_name, point)
        except Exception:
            untranslatable.append(f"{point_name}:context")
            continue
        thresholds.update({k: float(v) for k, v in point.thresholds.items()})

        if point.weights and not point.rules:
            # tali falls through to a weighted-score verdict when a point has
            # weights but no firing rule; mainspring has no equivalent
            # fallthrough, so a weights-only point can't be reproduced.
            untranslatable.append(f"{point_name}:weighted_fallthrough")

        base = _POINT_PRIORITY_BASE.get(point_name, 0)
        for rule in point.rules:
            expr = rule.if_

            def _when(_ctx: dict, _expr: str = expr, _pctx: dict = point_ctx) -> bool:
                # mainspring passes its own flattened ctx; tali's evaluator is
                # the source of truth for the expression language, so evaluate
                # the same expression against tali's per-point context.
                try:
                    return bool(_eval_condition(_expr, _pctx))
                except Exception:
                    return False

            rules.append(
                Rule(
                    name=f"{point_name}:{expr}",
                    when=_when,
                    then=rule.then,
                    # Preserve in-point priority order under the per-point base.
                    priority=base + int(rule.priority),
                    reason=rule.reason_template or "",
                    # auto_reject is tali's hard verb — mark terminal so
                    # mainspring's abstention overlay doesn't soften it.
                    terminal=(rule.then == "auto_reject"),
                )
            )

    engine = PolicyEngine(rules=rules, thresholds=thresholds)
    return engine, untranslatable


def _flags_for_engine(inputs: "DecisionInputs") -> dict[str, bool]:
    return {k: bool(v) for k, v in (inputs.flags or {}).items()}


def shadow_compare_verdict(
    *,
    inputs: "DecisionInputs",
    policy: "PolicyJson",
    tali_verdict: "PolicyDecision",
) -> None:
    """If policy shadow is on, re-derive the verdict through mainspring's
    vendored ``PolicyEngine`` (a DomainSpec translated from the same policy
    row + inputs) and log a verdict agreement/disagreement diff. Never raises.

    ``policy`` is the already-validated, overlay-applied ``PolicyJson`` tali
    evaluated; ``tali_verdict`` is the verdict tali emitted for ``inputs``.
    """
    if not getattr(settings, "MAINSPRING_POLICY_SHADOW", False):
        return
    try:
        from vendor.mainspring_policy.policy import PolicyEngine, Rule
        from vendor.mainspring_policy.signals import Signal, SignalBundle

        engine, untranslatable = _translate_to_engine(policy, inputs, PolicyEngine, Rule)
        bundle = _build_signal_bundle(inputs, SignalBundle, Signal)
        flags = _flags_for_engine(inputs)

        ms_verdict = engine.evaluate(
            str(getattr(inputs, "application_id", "?")),
            bundle,
            flags=flags,
        )

        tali_type = tali_verdict.decision_type
        ms_type = ms_verdict.decision_type
        # mainspring emits its own constants for the passive verbs; normalise
        # both sides to a comparable token before judging agreement.
        agree = tali_type == ms_type or (
            tali_type in _PASSIVE_VERBS and ms_type in _PASSIVE_VERBS
        )

        common = dict(
            event="mainspring_policy_shadow",
            decision_point=tali_verdict.decision_point,
            tali_decision_type=tali_type,
            mainspring_decision_type=ms_type,
            policy_revision_id=tali_verdict.policy_revision_id,
        )

        if untranslatable:
            # The row carried structure we couldn't faithfully translate, so a
            # mismatch here is inconclusive — flag it as a translation gap, the
            # analogue of metering's 'unpriced', not a real disagreement.
            logger.info(
                "mainspring_policy_shadow status=gap tali=%s mainspring=%s untranslatable=%s",
                tali_type, ms_type, ",".join(untranslatable),
                extra={**common, "status": "gap", "untranslatable": untranslatable},
            )
            return

        status = "agree" if agree else "disagree"
        logger.info(
            "mainspring_policy_shadow status=%s point=%s tali=%s mainspring=%s",
            status, tali_verdict.decision_point, tali_type, ms_type,
            extra={**common, "status": status},
        )
    except Exception:  # pragma: no cover — shadow must never affect the verdict
        logger.exception("mainspring_policy_shadow: comparison failed (non-fatal)")
