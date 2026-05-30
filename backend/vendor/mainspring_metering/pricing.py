"""Model pricing — the *one* source of truth.

Every cost number flows through ``cost_for(...)``. Updating Anthropic's
pricing is one edit here; no other module hard-codes a price.

Prices are in micro-USD per token (1 USD = 1_000_000 micro-USD). We
record four token streams separately because Anthropic prices them
distinctly: input, output, cache_read (5x cheaper than input), and
cache_creation (1.25x of input).

Aliases: ``claude-haiku-4-5-latest`` resolves to whatever dated model
that alias currently points to; the reconciler relies on this map to
fold alias drift (e.g. Anthropic re-pointed the latest tag) into
matching local records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelPrice:
    """Per-token prices in micro-USD."""
    input_per_token: float
    output_per_token: float
    cache_read_per_token: float
    cache_creation_per_token: float

    @classmethod
    def from_per_million(
        cls, *, input_usd: float, output_usd: float,
        cache_read_usd: float | None = None,
        cache_creation_usd: float | None = None,
    ) -> "ModelPrice":
        """Build from $/1M-tokens (the format Anthropic publishes)."""
        per_m = 1_000_000  # micro-USD per USD
        per_token = lambda usd: (usd * per_m) / 1_000_000  # noqa: E731
        cache_read = cache_read_usd if cache_read_usd is not None else input_usd * 0.1
        cache_create = cache_creation_usd if cache_creation_usd is not None else input_usd * 1.25
        return cls(
            input_per_token=per_token(input_usd),
            output_per_token=per_token(output_usd),
            cache_read_per_token=per_token(cache_read),
            cache_creation_per_token=per_token(cache_create),
        )


# ---------------------------------------------------------------------------
# Canonical price table  (update here when Anthropic changes pricing)
# ---------------------------------------------------------------------------
# Prices below reflect the public Anthropic pricing as of writing. Update
# in one place; ``cost_for`` reads from here.

_DATED_PRICES: dict[str, ModelPrice] = {
    # Claude Haiku family
    "claude-haiku-4-5-20251001": ModelPrice.from_per_million(
        input_usd=1.0, output_usd=5.0,
    ),
    "claude-3-5-haiku-20241022": ModelPrice.from_per_million(
        input_usd=0.80, output_usd=4.0,
    ),
    "claude-3-haiku-20240307": ModelPrice.from_per_million(
        input_usd=0.25, output_usd=1.25,
    ),
    # Claude Sonnet family
    "claude-sonnet-4-5-20250929": ModelPrice.from_per_million(
        input_usd=3.0, output_usd=15.0,
    ),
    "claude-3-5-sonnet-20241022": ModelPrice.from_per_million(
        input_usd=3.0, output_usd=15.0,
    ),
    # Claude Opus family
    "claude-opus-4-20250514": ModelPrice.from_per_million(
        input_usd=15.0, output_usd=75.0,
    ),
    "claude-3-opus-20240229": ModelPrice.from_per_million(
        input_usd=15.0, output_usd=75.0,
    ),
}


# Anthropic publishes alias tags that point to whichever dated model
# Anthropic currently considers "latest" in that family. Calls return
# the alias unchanged; we resolve it for pricing + alias-mismatch
# reconciliation.
_ALIASES: dict[str, str] = {
    "claude-haiku-4-5-latest": "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-latest": "claude-3-5-haiku-20241022",
    "claude-3-haiku-latest": "claude-3-haiku-20240307",
    "claude-sonnet-4-5-latest": "claude-sonnet-4-5-20250929",
    "claude-3-5-sonnet-latest": "claude-3-5-sonnet-20241022",
    "claude-opus-4-latest": "claude-opus-4-20250514",
}


# Public surface — modules outside this file read these constants.
PRICING: dict[str, ModelPrice] = dict(_DATED_PRICES)
ALIASES: dict[str, str] = dict(_ALIASES)


def resolve_model(model: str) -> str:
    """Turn an alias / latest tag into the dated model name. Pass-through
    for unknown strings so the reconciler can flag them."""
    return ALIASES.get(model, model)


def cost_for(
    *, model: str, input_tokens: int, output_tokens: int,
    cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
) -> int:
    """Compute the cost of one call in micro-USD. Returns 0 for unknown
    models (and the reconciler flags the call) — we never silently
    bill at an arbitrary rate."""
    dated = resolve_model(model)
    price = PRICING.get(dated)
    if price is None:
        return 0
    total = (
        input_tokens * price.input_per_token
        + output_tokens * price.output_per_token
        + cache_read_tokens * price.cache_read_per_token
        + cache_creation_tokens * price.cache_creation_per_token
    )
    return int(round(total))


def register_price(model: str, price: ModelPrice) -> None:
    """Register a custom price (used by tests + future model rollouts
    that haven't shipped yet at the time of the deploy)."""
    PRICING[model] = price


def supported_models() -> list[str]:
    """All dated model names we know how to price."""
    return sorted(PRICING)
