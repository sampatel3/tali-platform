"""Cutover bridge: drive tali's verdict through mainspring's vendored engine.

ADR-0010, decision-policy cut. tali's verdict cascade is no longer hand-rolled
inside ``engine.evaluate`` — it is produced by mainspring's vendored
``PolicyEngine.evaluate_decision_points`` (the weighted decision-point cascade
the substrate gained during convergence). This module is the thin translation
layer: tali ``PolicyJson`` + ``DecisionInputs`` → a list of mainspring
``DecisionPointSpec`` + a ``SignalBundle`` + flags + skip-points, then back to
a tali ``PolicyDecision``.

Parity discipline (why this is byte-identical to the old cascade):

* Rule firing is delegated to tali's OWN pure evaluator. Each mainspring
  ``WeightedRule.when`` closes over the *exact* per-point context tali built
  (``_build_rule_context`` + the per-point ``weighted_score``) and evaluates
  the rule string with tali's ``_eval_condition``. So control-flow/priority is
  mainspring's; the rule language stays tali's source of truth — no
  re-implementation, no drift.
* The weighted score + signal-density confidence mainspring computes off the
  ``SignalBundle`` are arithmetically identical to tali's ``_weighted_score`` /
  ``_confidence_from_inputs`` (scores at full confidence; graph priors lifted
  0..1→0..100 and carried at the prior's reported confidence — so a cold-start
  prior at confidence 0 collapses out of both the score and the density).
* ``confidence_floor`` and the ``auto_reject``-is-terminal bypass are carried
  per point, reproducing tali's floor gate.
* Manual-action skip becomes ``skip_points``; the cascade's
  "passive outcome ⇒ keep looking, first queue wins, else last passive" matches
  tali's ``DECISION_POINT_ORDER`` walk and ``last_no_action`` fallthrough.
* The abstention overlay is NOT used (we call ``evaluate_decision_points``,
  which has no escalation path) — matching tali, whose abstention lives
  downstream in ``policy_evaluator`` and is applied after this verdict.

No DB, no LLM, never raises to the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vendor.mainspring_policy.policy import DecisionPointSpec, PolicyEngine, WeightedRule
from vendor.mainspring_policy.signals import Signal, SignalBundle

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import DecisionInputs
    from .schema import PolicyJson


# tali's fixed decision-point cascade order (send before advance before reject).
_POINT_ORDER = ("send_assessment", "advance_to_interview", "reject")
# tali's hard verb — bypasses the confidence floor (certain by construction).
_TERMINAL_VERB = "auto_reject"


def build_signal_bundle(inputs: "DecisionInputs") -> SignalBundle:
    """tali score-like inputs → a mainspring ``SignalBundle``.

    Scores become signals at full confidence; graph priors are lifted
    0..1→0..100 and carry the prior's reported confidence (so a zeroed prior
    drops out of both the weighted score and the density confidence). Names are
    verbatim (graph priors namespaced ``graph_prior_<key>``) so the bundle's
    ``value(key)`` matches tali's ``_weighted_score`` ``raw`` exactly.
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


def flags_for_engine(inputs: "DecisionInputs") -> dict[str, bool]:
    return {k: bool(v) for k, v in (inputs.flags or {}).items()}


def build_decision_points(
    policy: "PolicyJson", inputs: "DecisionInputs"
) -> list[DecisionPointSpec]:
    """tali ``PolicyJson`` → ordered list of mainspring ``DecisionPointSpec``.

    Each tali rule becomes a ``WeightedRule`` whose ``when`` closes over tali's
    own per-point context, so the rule language never leaves tali. Points are
    emitted in tali's fixed cascade order; absent points are skipped.
    """
    # Imported here (not at module top) to avoid an import cycle with engine.py.
    from .engine import _build_rule_context, _eval_condition, _weighted_score

    points: list[DecisionPointSpec] = []
    for point_name in _POINT_ORDER:
        point = policy.decision_points.get(point_name)
        if point is None:
            continue

        # The exact per-point context tali's evaluator sees: scores + this
        # point's thresholds + namespaced graph priors + flags + intent_* +
        # the per-point weighted_score. Captured ONCE and closed over by every
        # rule predicate for this point.
        point_ctx = _build_rule_context(inputs, point_name, point)
        weighted_total, _contrib = _weighted_score(inputs, point)
        point_ctx["weighted_score"] = weighted_total

        rules: list[WeightedRule] = []
        for rule in point.rules:
            expr = rule.if_

            def _when(_ctx: dict, _expr: str = expr, _pctx: dict = point_ctx) -> bool:
                # Ignore mainspring's flat ctx; evaluate tali's expression
                # against tali's per-point context (the parity-critical bit).
                try:
                    return bool(_eval_condition(_expr, _pctx))
                except Exception:
                    return False

            rules.append(
                WeightedRule(
                    name=f"{point_name}:{expr}",
                    when=_when,
                    then=rule.then,
                    priority=int(rule.priority),
                    reason=rule.reason_template or "",
                    terminal=(rule.then == _TERMINAL_VERB),
                )
            )

        points.append(
            DecisionPointSpec(
                name=point_name,
                rules=rules,
                thresholds={k: float(v) for k, v in point.thresholds.items()},
                weights={k: float(v) for k, v in point.weights.items()},
                confidence_floor=float(point.confidence_floor),
            )
        )
    return points


def derive_verdict(
    inputs: "DecisionInputs",
    policy: "PolicyJson",
    *,
    skip_points: set[str],
) -> Any:
    """Run mainspring's vendored cascade over the translated policy.

    Returns the mainspring ``Verdict``. ``skip_points`` are the decision points
    a recent manual recruiter action supersedes (tali's manual-action skip).
    """
    points = build_decision_points(policy, inputs)
    bundle = build_signal_bundle(inputs)
    flags = flags_for_engine(inputs)
    engine = PolicyEngine(rules=[])  # flat-rule list unused; we drive the cascade
    return engine.evaluate_decision_points(
        str(getattr(inputs, "application_id", "?")),
        points,
        bundle,
        flags=flags,
        skip_points=skip_points,
    )


__all__ = [
    "build_decision_points",
    "build_signal_bundle",
    "derive_verdict",
    "flags_for_engine",
]
