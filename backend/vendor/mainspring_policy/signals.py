"""Signal producers — the pluggable "sub-agents" that score an entity.

A :class:`SignalProducer` turns an entity into one or more named
:class:`Signal` values with confidences. A producer can be anything: a
deterministic calculator (debt-to-income ratio), an API call (a KYC
vendor), a classical ML model, or an LLM scorer. The framework only cares
about the :class:`Signal` shape it returns.

Producers are intentionally dumb about each other — each is independent, so
the runtime can run them in any order (or in parallel) and compose the
results. Disagreement *between* producers is what the policy layer later
turns into an escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .budget import BudgetGovernor
from .pipeline import Entity


@dataclass
class Signal:
    """A single named measurement about an entity.

    ``value`` is conventionally 0..100 for scores or 0..1 for
    probabilities; ``confidence`` is always 0..1 and drives escalation.
    """

    name: str
    value: float | None = None
    confidence: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalBundle:
    """All signals gathered for one entity in one cycle."""

    signals: dict[str, Signal] = field(default_factory=dict)

    def add(self, signal: Signal) -> None:
        self.signals[signal.name] = signal

    def value(self, name: str, default: float | None = None) -> float | None:
        s = self.signals.get(name)
        return s.value if s is not None and s.value is not None else default

    def confidence(self, name: str, default: float = 0.0) -> float:
        s = self.signals.get(name)
        return s.confidence if s is not None else default

    def as_context(self) -> dict[str, Any]:
        """Flatten to ``{name: value}`` for the policy rule context."""
        return {n: s.value for n, s in self.signals.items() if s.value is not None}


@runtime_checkable
class SignalProducer(Protocol):
    name: str

    def produce(self, entity: Entity) -> dict[str, Signal]:  # pragma: no cover - protocol
        ...

    # Estimated cost in micro-USD of running this producer once. Lets the
    # budget governor meter every producer call. Default 0 (free / local).
    cost_micro_usd: int


def gather_signals(
    entity: Entity,
    producers: list[SignalProducer],
    *,
    budget: BudgetGovernor | None = None,
    persist: bool = True,
) -> SignalBundle:
    """Run every producer, merge results, and (optionally) write values
    back onto the entity so the next cycle's classifier can see them.

    Metering: each producer call is billed to the budget governor when one
    is supplied. A producer that raises is skipped (its absence simply
    lowers confidence downstream) — a flaky vendor never crashes a cycle.
    """
    bundle = SignalBundle()
    for producer in producers:
        cost = int(getattr(producer, "cost_micro_usd", 0) or 0)
        if budget is not None and cost:
            budget.meter(cost, label=f"signal:{producer.name}")
        try:
            for sig in producer.produce(entity).values():
                bundle.add(sig)
        except Exception:  # noqa: BLE001 - resilience is the point
            continue
    if persist:
        for name, sig in bundle.signals.items():
            if sig.value is not None:
                entity.set(f"signal::{name}", sig.value)
    return bundle
