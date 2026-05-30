"""Metering seam — the brand-agnostic, ORM-free surface a brand binds to.

This is the convergence seam (ADR-0010): the minimal, dependency-light
contract a brand (e.g. tali-platform) imports to price + normalise Anthropic
calls *through the substrate*, WITHOUT pulling in mainspring's
``Brand``/``Session``/ORM machinery. Cut #1 of the metering convergence uses it
for **shadow comparison** — the brand keeps its own metered client but computes
mainspring's cost on the same tokens and proves token+cost parity before any
cutover.

Importable standalone: depends only on ``.pricing`` (itself ORM-free) and the
stdlib, so a consumer can vendor ``seam.py`` + ``pricing.py`` and nothing else.
Deliberately imports no ``anthropic`` symbol (takes ``response: Any``) so it
sits cleanly on either side of the metering CI gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from .pricing import cost_for, resolve_model


@dataclass(frozen=True)
class TokenUsage:
    """The four token streams Anthropic prices distinctly, normalised from a
    provider ``response.usage`` block.

    ``cache_creation_1h_tokens`` is the slice of ``cache_creation`` written
    with a 1-hour TTL (priced higher than the 5-minute default); ``None`` means
    the provider didn't report the split.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_creation_1h_tokens: Optional[int] = None


def _as_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def extract_usage(response: Any) -> TokenUsage:
    """Pull the four token streams from an Anthropic ``messages`` response.

    Tolerant of object- or dict-shaped ``usage`` and a missing cache-creation
    TTL breakdown. Field names follow the Anthropic API: ``input_tokens``,
    ``output_tokens``, ``cache_read_input_tokens``,
    ``cache_creation_input_tokens``, and the optional
    ``cache_creation.ephemeral_1h_input_tokens`` slice.
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return TokenUsage()

    def g(name: str) -> Any:
        return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

    cc = g("cache_creation")
    cc_1h: Optional[int] = None
    if cc is not None:
        v = (
            cc.get("ephemeral_1h_input_tokens")
            if isinstance(cc, dict)
            else getattr(cc, "ephemeral_1h_input_tokens", None)
        )
        cc_1h = _as_int(v) if v is not None else None

    return TokenUsage(
        input_tokens=_as_int(g("input_tokens")),
        output_tokens=_as_int(g("output_tokens")),
        cache_read_tokens=_as_int(g("cache_read_input_tokens")),
        cache_creation_tokens=_as_int(g("cache_creation_input_tokens")),
        cache_creation_1h_tokens=cc_1h,
    )


def price_usage(model: str, usage: TokenUsage) -> int:
    """Cost of one call in micro-USD via the canonical ``cost_for`` — including
    the cache 1-hour-TTL split (2x input vs the 5m default at 1.25x) when the
    brand reported ``cache_creation_1h_tokens``."""
    return cost_for(
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cache_creation_1h_tokens=usage.cache_creation_1h_tokens,
    )


@dataclass
class MeteredOutcome:
    """What a :class:`MeteredCall` returns: the provider response, the
    normalised usage, and the priced micro-USD. Brand-specific persistence
    (markup, credits, Brand/case keys, ledger) is layered on by the
    implementation — it is NOT carried on the seam."""

    response: Any
    usage: TokenUsage
    cost_micro_usd: int
    skipped_for_budget: bool = False


@runtime_checkable
class MeteredCall(Protocol):
    """The convergence contract: a metered Anthropic call site, independent of
    how the underlying meter persists it (mainspring keys on ``Brand``/
    ``Session``; tali on org/role + its own tables). Both satisfy this shape,
    so call sites can bind to the seam and the implementation can be swapped
    underneath without touching them.
    """

    def metered_create(self, *, model: str, **anthropic_kwargs: Any) -> MeteredOutcome:
        ...


__all__ = [
    "TokenUsage",
    "extract_usage",
    "price_usage",
    "MeteredOutcome",
    "MeteredCall",
    "cost_for",
    "resolve_model",
]
