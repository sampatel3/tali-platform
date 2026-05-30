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


# Base-alias prices — the substrate must price the SAME model strings a brand
# bills. tali-platform rates on the snapshot-stripped BASE alias
# (``claude-haiku-4-5``, ``claude-sonnet-4-6``, …) and those rates are verified
# accurate vs Anthropic billing to ~1%. Mirror them here so the metering
# convergence shadow comparator goes green — a brand's bare alias otherwise
# prices to 0 ("unpriced"). Kept distinct from the dated table for clarity.
_BASE_PRICES: dict[str, ModelPrice] = {
    "claude-haiku-4-5":  ModelPrice.from_per_million(input_usd=1.0, output_usd=5.0),
    "claude-sonnet-4-5": ModelPrice.from_per_million(input_usd=3.0, output_usd=15.0),
    "claude-sonnet-4-6": ModelPrice.from_per_million(input_usd=3.0, output_usd=15.0),
    "claude-sonnet-4-7": ModelPrice.from_per_million(input_usd=3.0, output_usd=15.0),
    "claude-opus-4":     ModelPrice.from_per_million(input_usd=15.0, output_usd=75.0),
    "claude-opus-4-5":   ModelPrice.from_per_million(input_usd=15.0, output_usd=75.0),
    # legacy base aliases a brand keeps for historical recompute
    "claude-3-5-haiku":  ModelPrice.from_per_million(input_usd=0.80, output_usd=4.0),
    "claude-3-5-sonnet": ModelPrice.from_per_million(input_usd=3.0, output_usd=15.0),
    "claude-3-7-sonnet": ModelPrice.from_per_million(input_usd=3.0, output_usd=15.0),
    "claude-3-opus":     ModelPrice.from_per_million(input_usd=15.0, output_usd=75.0),
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


# Public surface — modules outside this file read these constants. Dated ids win
# on key collisions (none today); base aliases fill the gap for brand strings.
PRICING: dict[str, ModelPrice] = {**_BASE_PRICES, **_DATED_PRICES}
ALIASES: dict[str, str] = dict(_ALIASES)


def _strip_snapshot(model: str) -> str:
    """Strip a trailing ``-YYYYMMDD`` snapshot tag to the base alias
    (``claude-sonnet-4-5-20250929`` → ``claude-sonnet-4-5``). Mirrors the brand
    convention so a dated id we don't carry still prices off its base entry."""
    if not model:
        return ""
    head, _, tail = model.rpartition("-")
    return head if (head and len(tail) == 8 and tail.isdigit()) else model


def resolve_model(model: str) -> str:
    """Resolve an alias / latest tag / dated snapshot to a priced key.

    Order is additive so existing reconciler behaviour for KNOWN dated ids is
    unchanged (they return as-is): explicit alias → exact PRICING hit → strip a
    ``-YYYYMMDD`` snapshot to a base/aliased entry → pass through (the reconciler
    then flags a truly unknown model)."""
    if model in ALIASES:
        return ALIASES[model]
    if model in PRICING:
        return model
    base = _strip_snapshot(model)
    if base != model and (base in PRICING or base in ALIASES):
        return ALIASES.get(base, base)
    return model


def cost_for(
    *, model: str, input_tokens: int, output_tokens: int,
    cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
    cache_creation_1h_tokens: Optional[int] = None,
) -> int:
    """Compute the cost of one call in micro-USD. Returns 0 for unknown
    models (and the reconciler flags the call) — we never silently
    bill at an arbitrary rate.

    ``cache_creation_1h_tokens`` is the slice of ``cache_creation_tokens``
    written with a 1-hour TTL, which Anthropic prices at 2x input (vs the 5m
    default at 1.25x). ``None`` prices the whole cache-creation stream at the
    5m rate — the conservative, backward-compatible default."""
    dated = resolve_model(model)
    price = PRICING.get(dated)
    if price is None:
        return 0
    if cache_creation_1h_tokens is None:
        cache_creation_cost = cache_creation_tokens * price.cache_creation_per_token
    else:
        cc_1h = int(cache_creation_1h_tokens or 0)
        cc_5m = cache_creation_tokens - cc_1h
        # 1h-TTL writes price at 2x input; the 5m remainder at the 1.25x
        # cache_creation rate. Matches the brand meter token-for-token.
        cache_creation_cost = (
            cc_5m * price.cache_creation_per_token
            + cc_1h * price.input_per_token * 2.0
        )
    total = (
        input_tokens * price.input_per_token
        + output_tokens * price.output_per_token
        + cache_read_tokens * price.cache_read_per_token
        + cache_creation_cost
    )
    return int(round(total))


def register_price(model: str, price: ModelPrice) -> None:
    """Register a custom price (used by tests + future model rollouts
    that haven't shipped yet at the time of the deploy)."""
    PRICING[model] = price


def supported_models() -> list[str]:
    """All dated model names we know how to price."""
    return sorted(PRICING)
