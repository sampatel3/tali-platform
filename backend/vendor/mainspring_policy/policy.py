"""VENDORED from mainspring (ADR-0010, decision-policy). DO NOT EDIT BY HAND.

Assembled flat from mainspring/spec/policy_types.py (pure types) +
mainspring/governance/policy.py (engine logic). Re-vendor via
backend/scripts/vendor_mainspring_policy.sh."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable, Protocol, runtime_checkable

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


@runtime_checkable
class PolicyEngineProtocol(Protocol):
    """Structural contract for a deterministic verdict engine.

    The concrete :class:`mainspring.core.policy.PolicyEngine` satisfies this.
    :class:`~mainspring.spec.domain.DomainSpec` types its ``.policy`` field
    against this protocol so that spec carries no dependency on the
    governance-layer engine logic — an adopter supplies a concrete engine at
    construction time and it satisfies the contract at runtime.
    """

    version: int
    rules: list["Rule"]

    def evaluate(
        self,
        entity_id: str,
        signals: "SignalBundle",
        *,
        flags: dict[str, bool] | None = None,
        has_pending_decision: bool = False,
        recent_human_action: str | None = None,
    ) -> "Verdict": ...


# ---------------------------------------------------------------------------
# Weighted decision points (ADR-0010 convergence)
# ---------------------------------------------------------------------------
#
# A flat ``[Rule, ...]`` list is enough for a single decision (the original
# ``PolicyEngine.evaluate`` path). Some adopters — recruitment being the
# canonical one — model a *cascade* of named decision points (e.g.
# ``send_assessment`` then ``advance`` then ``reject``), where each point
# carries its own rules AND a ``weights`` map used for a confidence measure
# and a weighted-score fallthrough. The cascade returns the first point that
# yields a queueing verdict; passive (skip / no_action) outcomes do NOT end
# the cascade, they let it keep looking.
#
# ``DecisionPointSpec`` + ``PolicyEngine.evaluate_decision_points`` reproduce
# that behaviour *exactly* and ORM-free, so a vertical that needs it can drive
# the same kernel rather than hand-rolling a second engine. Rule-only adopters
# are unaffected — this is purely additive; the flat ``evaluate`` path is
# untouched.


# Rule verbs that make a decision point return WITHOUT queueing. They do not
# end the cascade — the engine records the outcome and keeps looking at later
# points (matching the recruitment engine's "stale higher-priority skip must
# not block a lower point" semantics).
_PASSIVE_VERBS = (SKIP, NO_ACTION)


@dataclass
class WeightedRule:
    """A rule inside a :class:`DecisionPointSpec`.

    Identical in spirit to :class:`~mainspring.spec.policy_types.Rule` but the
    predicate is evaluated against the *point's* context (signals + that
    point's thresholds + the point's weighted score + flags), and ``then`` may
    be any adopter verb string (not just SKIP/NO_ACTION) — the cascade decides
    which verbs are "queueing" vs "passive".

    ``terminal`` rules bypass the confidence floor: a hard/terminal verdict is
    certain by construction and must not be diluted by missing signals.
    """

    name: str
    when: Callable[[dict[str, Any]], bool]
    then: str
    priority: int = 0
    reason: str = ""
    terminal: bool = False


@dataclass
class DecisionPointSpec:
    """One named decision point in a cascade.

    ``weights`` map signal names to floats; they drive (a) a *signal-density*
    confidence — fraction of weighted signals present — and (b) a weighted
    score exposed to rules as ``weighted_score``. ``confidence_floor`` is the
    minimum confidence a *queueing* (non-passive, non-terminal) verdict must
    clear before it fires; below it the point yields a passive ``no_action``.
    """

    name: str
    rules: list[WeightedRule] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    confidence_floor: float = 0.0


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

    # -- weighted decision-point cascade -----------------------------------
    #
    # The flat ``evaluate`` above answers ONE decision. ``evaluate_decision_points``
    # answers a *cascade* of named points, each with its own weighted score +
    # confidence floor, returning the first point that queues. This is the
    # behaviour the recruitment vertical needs; it reuses the same predicate /
    # priority / first-match-wins discipline so there is a single verdict kernel.

    @staticmethod
    def _point_weighted_score(
        point: "DecisionPointSpec", signals: SignalBundle
    ) -> tuple[float, dict[str, float]]:
        """Weighted score over a point's ``weights``.

        Missing signals contribute 0. ``signals`` is assumed to already carry
        the adopter's value convention (e.g. probability priors lifted to a
        score scale) so the math here is a plain ``Σ value·weight`` — exactly
        the form the recruitment engine uses.
        """
        contributions: dict[str, float] = {}
        total = 0.0
        for key, weight in point.weights.items():
            raw = signals.value(key)
            raw = 0.0 if raw is None else float(raw)
            contribution = raw * float(weight)
            contributions[key] = contribution
            total += contribution
        return total, contributions

    @staticmethod
    def _point_confidence(
        point: "DecisionPointSpec", signals: SignalBundle
    ) -> float:
        """Confidence = fraction of this point's weighted signals present.

        A signal counts as present iff it is in the bundle with a value AND a
        positive confidence (so a zeroed-out / cold-start prior, carried at
        confidence 0, collapses cleanly). With no weights, confidence is 0.0.
        """
        if not point.weights:
            return 0.0
        present = 0
        for key in point.weights.keys():
            sig = signals.signals.get(key)
            if sig is not None and sig.value is not None and sig.confidence > 0.0:
                present += 1
        return present / len(point.weights)

    def evaluate_decision_points(
        self,
        entity_id: str,
        points: "list[DecisionPointSpec]",
        signals: SignalBundle,
        *,
        flags: dict[str, bool] | None = None,
        queueing_verbs: "frozenset[str] | set[str] | None" = None,
        skip_points: "set[str] | None" = None,
    ) -> Verdict:
        """Evaluate a cascade of weighted decision points; first queue wins.

        For each point, in the order given:

          * if the point is in ``skip_points`` (an upstream manual/HITL
            override) → emit a passive SKIP for that point and keep looking;
          * else walk its rules priority-descending, first match wins:
              - a PASSIVE verb (``skip`` / ``no_action``) → record + keep looking;
              - a queueing verb below the confidence floor (and not terminal)
                → demote to ``no_action`` + keep looking;
              - otherwise → that verb is the verdict, cascade stops.
          * a point whose rules don't fire falls through to ``no_action`` +
            keep looking (NO synthesized weighted verdict — the weighted score
            is exposed to rules as ``weighted_score`` and recorded in the
            reasoning, but a point with weights-but-no-firing-rule is passive).

        If no point queues, the last passive outcome is returned (or a bare
        ``no_action`` when there were no points at all). The abstention overlay
        is intentionally NOT applied here — a cascade adopter folds its own
        uncertainty handling into ``confidence_floor`` + its rules.

        ``queueing_verbs`` lists the verbs that END the cascade. Anything not
        in it (besides the passive verbs) is still treated as a final verdict
        UNLESS it is passive — i.e. by default every non-passive verb queues.
        Pass an explicit set to be strict.
        """
        flags = flags or {}
        skip_points = skip_points or set()

        last_passive: Verdict | None = None
        for point in points:
            v = self._evaluate_one_point(
                entity_id,
                point,
                signals,
                flags=flags,
                queueing_verbs=queueing_verbs,
                skipped=point.name in skip_points,
            )
            if v.decision_type in _PASSIVE_VERBS:
                last_passive = v
                continue
            return v

        if last_passive is not None:
            return last_passive
        return Verdict(
            decision_type=NO_ACTION,
            reasoning="No decision points configured.",
            rule_path=["empty_policy"],
        )

    def _evaluate_one_point(
        self,
        entity_id: str,
        point: "DecisionPointSpec",
        signals: SignalBundle,
        *,
        flags: dict[str, bool],
        queueing_verbs: "frozenset[str] | set[str] | None",
        skipped: bool,
    ) -> Verdict:
        rule_path: list[str] = [f"point:{point.name}"]
        confidence = self._point_confidence(point, signals)

        if skipped:
            return Verdict(
                decision_type=SKIP,
                confidence=confidence,
                reasoning=(
                    f"An upstream action supersedes the {point.name} verdict — "
                    "skipping."
                ),
                rule_path=rule_path + ["manual_action_skip"],
                suppressed_reason="human_action",
            )

        # Per-point context: thresholds + signal values + flags + weighted score.
        ctx: dict[str, Any] = {}
        ctx.update({k: float(v) for k, v in point.thresholds.items()})
        ctx.update(signals.as_context())
        ctx.update({k: bool(v) for k, v in flags.items()})
        weighted_total, _contrib = self._point_weighted_score(point, signals)
        ctx["weighted_score"] = weighted_total

        for rule in sorted(point.rules, key=lambda r: -r.priority):
            try:
                fired = bool(rule.when(ctx))
            except Exception:  # noqa: BLE001 - a broken rule never crashes a cycle
                fired = False
            rule_path.append(f"{'✓' if fired else '·'} {rule.name}")
            if not fired:
                continue
            if rule.then in _PASSIVE_VERBS:
                return Verdict(
                    decision_type=rule.then,
                    confidence=confidence,
                    reasoning=rule.reason or f"Rule '{rule.name}' → {rule.then}",
                    evidence=dict(ctx),
                    rule_path=rule_path,
                )
            # Confidence floor gates queueing verbs; terminal rules bypass it.
            if not rule.terminal and confidence < point.confidence_floor:
                return Verdict(
                    decision_type=NO_ACTION,
                    confidence=confidence,
                    reasoning=(
                        f"Rule '{rule.name}' → {rule.then} but confidence "
                        f"{confidence:.2f} below floor {point.confidence_floor:.2f}; "
                        "not queueing."
                    ),
                    evidence=dict(ctx),
                    rule_path=rule_path + [f"confidence_floor_blocked:{confidence:.2f}"],
                )
            return Verdict(
                decision_type=rule.then,
                confidence=confidence,
                reasoning=rule.reason or f"Rule '{rule.name}' → {rule.then}",
                evidence=dict(ctx),
                rule_path=rule_path,
            )

        # No rule fired: passive no_action (weighted score recorded, not queued).
        rule_path.append(f"no_rule_matched:weighted={weighted_total:.2f}")
        return Verdict(
            decision_type=NO_ACTION,
            confidence=confidence,
            reasoning=(
                f"No rule matched in {point.name}. Weighted score "
                f"{weighted_total:.2f}; thresholds={dict(point.thresholds)}."
            ),
            evidence=dict(ctx),
            rule_path=rule_path,
        )


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
