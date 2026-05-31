"""The deterministic policy engine — the core of the secret sauce.

The LLM (or any planner) decides *where to spend the cycle*. It does **not**
decide the verdict. Verdicts come from this pure, deterministic engine:
declarative rules walked in priority order, falling through to weighted
thresholds, with an *abstention* overlay that escalates to a human when the
signal producers disagree or are individually unsure.

Why deterministic: verdicts must be auditable, reproducible, and immune to
prompt drift. In regulated domains (lending, claims) a hallucinated verdict
is unacceptable; the rule path *is* the explanation a regulator can read.
The LLM's judgement is upstream (which entities to look at, how to phrase a
question); the decision itself is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable

from .signals import SignalBundle

SKIP = "skip"
NO_ACTION = "no_action"
ESCALATE = "escalate"


@dataclass
class Verdict:
    decision_type: str
    confidence: float = 0.0
    reasoning: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    rule_path: list[str] = field(default_factory=list)
    escalated: bool = False
    # Set when a prior pending decision or recent human action suppressed
    # the verdict — the audit trail records *why* the operator stayed quiet.
    suppressed_reason: str | None = None


@dataclass
class Rule:
    """A declarative if/then. ``when`` is a predicate over a flat context
    (signal values + thresholds + entity flags); ``then`` is the
    decision_type to emit (or ``skip`` / ``no_action``).

    ``terminal`` marks a hard rule that bypasses the abstention overlay —
    a confirmed compliance failure (sanctions, fraud over tolerance) is
    never a judgement call, so it must not be softened into an escalation.
    """

    name: str
    when: Callable[[dict[str, Any]], bool]
    then: str
    priority: int = 0
    reason: str = ""
    terminal: bool = False


@dataclass
class EscalationConfig:
    """When to abstain and hand to a human instead of deciding."""

    per_signal_uncertainty_threshold: float = 0.5  # any signal below this conf
    disagreement_delta: float = 35.0  # max-min spread across scored signals
    watch_signals: tuple[str, ...] = ()  # signals whose disagreement matters
    enabled: bool = True


@dataclass
class PolicyEngine:
    """Deterministic verdict engine."""

    rules: list[Rule]
    thresholds: dict[str, float] = field(default_factory=dict)
    escalation: EscalationConfig = field(default_factory=EscalationConfig)
    version: int = 1

    def evaluate(
        self,
        entity_id: str,
        signals: SignalBundle,
        *,
        flags: dict[str, bool] | None = None,
        has_pending_decision: bool = False,
        recent_human_action: str | None = None,
    ) -> Verdict:
        # 1. Suppression — never re-decide an entity the human is already
        #    looking at, or one they just acted on themselves. This is the
        #    generic form of "don't double-queue / respect the override".
        if has_pending_decision:
            return Verdict(
                decision_type=SKIP,
                reasoning="A decision is already pending for this entity.",
                rule_path=["suppressed:pending"],
                suppressed_reason="pending",
            )
        if recent_human_action:
            return Verdict(
                decision_type=SKIP,
                reasoning=f"Human recently acted ({recent_human_action}).",
                rule_path=["suppressed:human_action"],
                suppressed_reason="human_action",
            )

        ctx: dict[str, Any] = {}
        ctx.update(self.thresholds)
        ctx.update(signals.as_context())
        ctx.update({k: bool(v) for k, v in (flags or {}).items()})

        confidence = self._confidence(signals)

        # 2. Rules in priority-descending order; first match wins.
        rule_path: list[str] = []
        for rule in sorted(self.rules, key=lambda r: -r.priority):
            try:
                fired = bool(rule.when(ctx))
            except Exception:  # noqa: BLE001 - a broken rule never crashes a cycle
                fired = False
            rule_path.append(f"{'✓' if fired else '·'} {rule.name}")
            if not fired:
                continue
            if rule.then in (SKIP, NO_ACTION):
                return Verdict(
                    decision_type=rule.then,
                    confidence=confidence,
                    reasoning=rule.reason or f"Rule '{rule.name}' → {rule.then}",
                    evidence=dict(ctx),
                    rule_path=rule_path,
                )
            # 3. Abstention overlay — a confident-looking rule still yields
            #    to a human when the underlying signals disagree or are
            #    unsure. Hard/terminal rules (sanctions, fraud) bypass it:
            #    those failures are not judgement calls.
            esc = None if rule.terminal else self._should_escalate(signals)
            if esc is not None:
                return Verdict(
                    decision_type=ESCALATE,
                    confidence=confidence,
                    reasoning=f"Rule '{rule.name}' would {rule.then}, but {esc}",
                    evidence=dict(ctx),
                    rule_path=rule_path + [f"escalate:{esc}"],
                    escalated=True,
                )
            return Verdict(
                decision_type=rule.then,
                confidence=confidence,
                reasoning=rule.reason or f"Rule '{rule.name}' → {rule.then}",
                evidence=dict(ctx),
                rule_path=rule_path,
            )

        # 4. No rule fired.
        return Verdict(
            decision_type=NO_ACTION,
            confidence=confidence,
            reasoning="No rule matched.",
            evidence=dict(ctx),
            rule_path=rule_path + ["no_rule_matched"],
        )

    # -- abstention helpers -------------------------------------------------

    def _should_escalate(self, signals: SignalBundle) -> str | None:
        cfg = self.escalation
        if not cfg.enabled:
            return None
        # (a) any individual signal below the confidence floor
        for name, sig in signals.signals.items():
            if sig.confidence < cfg.per_signal_uncertainty_threshold:
                return (
                    f"signal '{name}' confidence {sig.confidence:.2f} "
                    f"< {cfg.per_signal_uncertainty_threshold:.2f}"
                )
        # (b) sharp disagreement across the watched scored signals
        watch = cfg.watch_signals or tuple(signals.signals.keys())
        vals = [
            signals.value(n)
            for n in watch
            if signals.value(n) is not None
        ]
        if len(vals) >= 2:
            spread = max(vals) - min(vals)
            if spread > cfg.disagreement_delta:
                return f"signals disagree (spread {spread:.0f} > {cfg.disagreement_delta:.0f})"
        return None

    def _confidence(self, signals: SignalBundle) -> float:
        """Signal-density confidence: fraction of watched signals present."""
        watch = self.escalation.watch_signals or tuple(signals.signals.keys())
        if not watch:
            return 0.0
        present = sum(1 for n in watch if signals.value(n) is not None)
        return present / len(watch)


def org_wide_send_bar(
    advanced_scores: list[float],
    *,
    floor: float = 50.0,
    sigma_below: float = 1.0,
) -> float:
    """A dynamic, absolute threshold derived from how the *best* entities
    have historically scored — ``median(advanced) - σ``, with a hard floor.

    Generalised from the recruitment "send the top ~20% of survivors" bar.
    Absolute (not a per-batch percentile) so a weak batch doesn't force a
    fixed share through, and a strong batch isn't penalised. Self-tracks
    quality over time; the floor guards against a wholly-weak pipeline.
    """
    if len(advanced_scores) < 3:
        return floor
    m = median(advanced_scores)
    if len(advanced_scores) >= 2:
        mean = sum(advanced_scores) / len(advanced_scores)
        var = sum((x - mean) ** 2 for x in advanced_scores) / len(advanced_scores)
        sigma = var ** 0.5
    else:
        sigma = 0.0
    return max(floor, m - sigma_below * sigma)
