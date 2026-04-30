"""Single source of truth for usage-based pricing.

All accounting in **micro-credits**: 1 credit = $0.000001 USD. Integer math
everywhere; no float drift. Display layer divides by 1_000_000 for USD.

Two layers:
- Raw Claude cost: (in_tokens × input_rate + out_tokens × output_rate)
- Charged credits: raw_cost × markup_multiplier (per feature)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from enum import Enum
from typing import Optional

from ..platform.config import settings


CREDITS_PER_USD = 1_000_000


class Feature(str, Enum):
    PRESCREEN = "prescreen"
    SCORE = "score"
    ASSESSMENT = "assessment"
    OTHER = "other"


@dataclass(frozen=True)
class FeaturePricing:
    feature: Feature
    markup_multiplier: Decimal
    cache_hit_multiplier: Decimal  # applied when result served from cv_score_cache


@dataclass(frozen=True)
class CreditPack:
    pack_id: str
    label: str
    price_usd: int  # whole dollars (display)
    price_usd_cents: int  # for Stripe (cents)
    credits_granted: int  # micro-credits
    bonus_pct: int  # display only (already baked into credits_granted)


@dataclass(frozen=True)
class FreeTierGrant:
    credits: int  # micro-credits granted on signup
    description: str  # surfaced on landing & register


# ---- Feature pricing table -------------------------------------------------

# Prescreen at cost (1×). Scoring & assessments at 3× per the 2026-04-29 plan.
# Cache-hit multiplier is 0.10 — mirrors Anthropic's own cache-read pricing
# and prevents unlimited free re-scoring across orgs while still giving a
# meaningful discount when the work was already done.
_FEATURE_PRICING: dict[Feature, FeaturePricing] = {
    Feature.PRESCREEN: FeaturePricing(
        feature=Feature.PRESCREEN,
        markup_multiplier=Decimal("1.0"),
        cache_hit_multiplier=Decimal("0.10"),
    ),
    Feature.SCORE: FeaturePricing(
        feature=Feature.SCORE,
        markup_multiplier=Decimal("3.0"),
        cache_hit_multiplier=Decimal("0.10"),
    ),
    Feature.ASSESSMENT: FeaturePricing(
        feature=Feature.ASSESSMENT,
        markup_multiplier=Decimal("3.0"),
        cache_hit_multiplier=Decimal("0.10"),
    ),
    Feature.OTHER: FeaturePricing(
        feature=Feature.OTHER,
        markup_multiplier=Decimal("1.0"),
        cache_hit_multiplier=Decimal("0.10"),
    ),
}


def feature_pricing(feature: Feature | str) -> FeaturePricing:
    if isinstance(feature, str):
        feature = Feature(feature)
    return _FEATURE_PRICING[feature]


# ---- Free tier -------------------------------------------------------------

# 1 job, 100 candidates prescreened, ~30 scored, 3 assessments — sized to
# ~$0.40 of real Claude cost. Round to $1.50 for breathing room.
FREE_TIER = FreeTierGrant(
    credits=1_500_000,  # $1.50
    description="$1.50 free credits — try the full platform without a credit card",
)


# ---- Top-up packs (Stripe one-time payments) -------------------------------

CREDIT_PACKS: tuple[CreditPack, ...] = (
    CreditPack(
        pack_id="starter_20",
        label="Starter",
        price_usd=20,
        price_usd_cents=2_000,
        credits_granted=20_000_000,  # $20 face value
        bonus_pct=0,
    ),
    CreditPack(
        pack_id="growth_100",
        label="Growth",
        price_usd=100,
        price_usd_cents=10_000,
        credits_granted=110_000_000,  # $110 face value (10% bonus)
        bonus_pct=10,
    ),
    CreditPack(
        pack_id="scale_500",
        label="Scale",
        price_usd=500,
        price_usd_cents=50_000,
        credits_granted=600_000_000,  # $600 face value (20% bonus)
        bonus_pct=20,
    ),
)


def resolve_pack(pack_id: str) -> Optional[CreditPack]:
    for pack in CREDIT_PACKS:
        if pack.pack_id == pack_id:
            return pack
    return None


# ---- Cost / credits math ---------------------------------------------------

def raw_cost_usd_micro(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> int:
    """Compute Claude cost in micro-USD (millionths of a dollar).

    Anthropic prompt-cache pricing:
    - cache_read_tokens: 10% of input rate (cache hit on Anthropic's side)
    - cache_creation_tokens: 125% of input rate (one-time write cost)

    We charge customers based on Anthropic's bill, so we mirror these rates.
    """
    input_rate = Decimal(str(settings.CLAUDE_INPUT_COST_PER_MILLION_USD))
    output_rate = Decimal(str(settings.CLAUDE_OUTPUT_COST_PER_MILLION_USD))

    standard_input = Decimal(input_tokens) * input_rate
    standard_output = Decimal(output_tokens) * output_rate
    cache_read = Decimal(cache_read_tokens) * input_rate * Decimal("0.10")
    cache_creation = Decimal(cache_creation_tokens) * input_rate * Decimal("1.25")

    total_usd = (standard_input + standard_output + cache_read + cache_creation) / Decimal(1_000_000)
    micro = total_usd * Decimal(1_000_000)
    return int(micro.quantize(Decimal("1"), rounding=ROUND_UP))


def credits_charged(
    *,
    feature: Feature | str,
    cost_usd_micro: int,
    cache_hit: bool = False,
) -> int:
    """Apply per-feature markup. cost_usd_micro is the raw Claude cost in
    micro-USD; output is the credits to deduct from the org's balance.

    Cache hits (full result served from cv_score_cache, no Claude call)
    use a reduced multiplier to acknowledge the work was already done
    while still charging something to prevent unlimited free re-scoring.
    """
    pricing = feature_pricing(feature)
    multiplier = pricing.cache_hit_multiplier if cache_hit else pricing.markup_multiplier
    charged = Decimal(cost_usd_micro) * multiplier
    return int(charged.quantize(Decimal("1"), rounding=ROUND_UP))


def estimate_reservation(feature: Feature | str) -> int:
    """Pre-flight reservation for a feature. Use historical p95-ish numbers
    so we rarely under-reserve. Reconciled to actuals after the call.

    These are rough; tune later from `usage_events` percentiles.
    """
    estimates = {
        Feature.PRESCREEN: 1_500,    # ~$0.0015
        Feature.SCORE: 30_000,       # ~$0.03 (3× markup)
        Feature.ASSESSMENT: 60_000,  # ~$0.06 per Claude turn (3× markup)
        Feature.OTHER: 5_000,
    }
    if isinstance(feature, str):
        feature = Feature(feature)
    return estimates[feature]


def credits_to_usd_str(credits: int) -> str:
    """Display helper: 1_500_000 → '$1.50'. Two decimals, USD."""
    dollars = Decimal(credits) / Decimal(CREDITS_PER_USD)
    return f"${dollars.quantize(Decimal('0.01'))}"
