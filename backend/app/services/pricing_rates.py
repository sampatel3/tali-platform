"""Per-model Claude + Voyage rate tables and resolution helpers.

Split out of ``pricing_service`` to keep that module under the file-size gate.
This holds the raw rate data and the model→rate resolution logic; the markup,
credit-pack, and cost-math layers stay in ``pricing_service`` and re-export the
public names here (``CREDITS_PER_USD``, ``is_voyage_model``, ``voyage_cost_micro``).
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_UP
from typing import Optional

from ..platform.config import settings

logger = logging.getLogger("taali.pricing")


CREDITS_PER_USD = 1_000_000


# ---- Per-model Anthropic rates (USD per million tokens) -------------------
# Keyed by the base alias (the model id without the ``-YYYYMMDD`` snapshot
# suffix). Anthropic returns the dated snapshot id (e.g. ``claude-sonnet-4-5-20250929``);
# ``_resolve_model_rates`` strips it before lookup so a model rev change
# doesn't silently fall back to the env-var Haiku default.
#
# The historical bug (fixed 2026-05-26): ``raw_cost_usd_micro`` used a single
# global env-var rate ($1 input / $5 output — Haiku's), so every Sonnet call
# was booked at ~⅓ of its real cost. Reconciliation against Anthropic billing
# showed -34% on Sonnet for weeks before this was caught.
#
# Source: https://www.anthropic.com/pricing — keep aligned when Anthropic
# changes rates. cache_read = 0.10× input rate, cache_creation = 1.25× input
# rate (handled below, applies uniformly across the family).
_MODEL_RATES: dict[str, tuple[Decimal, Decimal]] = {
    # Claude 4.5 family
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "claude-sonnet-4-5": (Decimal("3"), Decimal("15")),
    # Claude 4.6 / 4.7 — Sonnet 4.6+ keeps the Sonnet-family price point;
    # add Opus when we start using it.
    "claude-sonnet-4-6": (Decimal("3"), Decimal("15")),
    "claude-sonnet-4-7": (Decimal("3"), Decimal("15")),
    "claude-opus-4": (Decimal("15"), Decimal("75")),
    "claude-opus-4-5": (Decimal("15"), Decimal("75")),
    # Legacy / pre-4.5 — kept for historical recompute. New code shouldn't
    # call these models.
    "claude-3-5-haiku": (Decimal("0.80"), Decimal("4")),
    "claude-3-5-sonnet": (Decimal("3"), Decimal("15")),
    "claude-3-7-sonnet": (Decimal("3"), Decimal("15")),
    "claude-3-opus": (Decimal("15"), Decimal("75")),
}


def _strip_snapshot_suffix(model: str) -> str:
    """Strip a trailing ``-YYYYMMDD`` snapshot tag from a model id.

    Anthropic publishes a snapshot suffix (e.g. ``claude-sonnet-4-5-20250929``)
    that drifts forward as they cut new versions of the same model. We rate
    on the base alias so a new snapshot doesn't trigger the fallback path.
    """
    if not model:
        return ""
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[0]
    return model


def _resolve_model_rates(model: Optional[str]) -> tuple[Decimal, Decimal]:
    """Return ``(input_rate, output_rate)`` per MTok for ``model``.

    Strips the snapshot suffix and looks up the base alias. Unknown models
    fall back to the env-var defaults with a logged warning — so a new
    family doesn't silently mis-price, but the system stays runnable.
    """
    base = _strip_snapshot_suffix(model or "")
    rates = _MODEL_RATES.get(base)
    if rates is not None:
        return rates
    # Unknown model: surface a warning so we add it to the table before
    # spend ramps. Default to env-var values for backwards compat.
    if model:
        logger.warning(
            "pricing: no rate table entry for model=%r (base=%r) — "
            "falling back to env-var defaults. Add it to _MODEL_RATES.",
            model, base,
        )
    return (
        Decimal(str(settings.CLAUDE_INPUT_COST_PER_MILLION_USD)),
        Decimal(str(settings.CLAUDE_OUTPUT_COST_PER_MILLION_USD)),
    )


# Voyage AI embedding rates ($ per 1M tokens). Anthropic has no embeddings API
# and recommends Voyage; Graphiti uses it for the knowledge-graph vector layer
# (the LLM extraction stays on Anthropic/Haiku, already metered). Embeddings
# bill on INPUT tokens ONLY — no output, no cache, no service tier. Numerically
# $X per 1M tokens == X micro-USD per token, so cost_usd_micro = tokens * rate.
_VOYAGE_RATES: dict[str, Decimal] = {
    "voyage-3": Decimal("0.06"),
    "voyage-3.5": Decimal("0.06"),
    "voyage-3-large": Decimal("0.18"),
    "voyage-3.5-lite": Decimal("0.02"),
    "voyage-3-lite": Decimal("0.02"),
    "voyage-2": Decimal("0.10"),
    "voyage-code-2": Decimal("0.12"),
    "voyage-finance-2": Decimal("0.12"),
    "voyage-law-2": Decimal("0.12"),
}
_VOYAGE_DEFAULT_RATE = Decimal("0.06")  # voyage-3 price point


def is_voyage_model(model: Optional[str]) -> bool:
    """True for Voyage embedding models — they price on a separate (non-Anthropic)
    rate table and are excluded from the Anthropic Admin-API reconciliation."""
    return bool(model) and model.lower().strip().startswith("voyage")


def voyage_cost_micro(*, model: Optional[str], input_tokens: int) -> int:
    """Voyage embedding cost in micro-USD (input tokens only, ROUND_UP)."""
    rate = _VOYAGE_RATES.get((model or "").lower().strip(), _VOYAGE_DEFAULT_RATE)
    micro = Decimal(int(input_tokens or 0)) * rate
    return int(micro.quantize(Decimal("1"), rounding=ROUND_UP))
