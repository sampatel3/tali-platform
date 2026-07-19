"""Per-model Claude pricing in ``raw_cost_usd_micro``.

Before 2026-05-26, ``raw_cost_usd_micro`` applied a single global env-var
rate ($1 input / $5 output — Haiku's) to every model. Sonnet 4.5 calls
were therefore booked at ~⅓ of their real cost, producing a systematic
-34% drift in the Anthropic reconciliation report for weeks. The bug
was caught when 2026-05-25 reconciliation showed Sonnet anthropic_usd
$27.36 vs internal $18.15 — the token totals matched Anthropic exactly,
which meant the leak was in the cost calc, not the meter.

These tests pin per-model rates so a future env-var-default regression
can't silently re-introduce the same bug.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.llm.models import FAST_MODEL, SONNET_MODEL
from app.platform.config import Settings
from app.services.claude_model_pricing import (
    UnpriceableClaudeModelError,
    is_priceable_claude_model,
    require_priceable_claude_model,
)
from app.services.pricing_service import (
    _MODEL_RATES,
    _resolve_model_rates,
    _strip_snapshot_suffix,
    raw_cost_usd_micro,
)


def test_strip_snapshot_suffix_handles_dated_and_aliased_ids():
    assert _strip_snapshot_suffix("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"
    assert _strip_snapshot_suffix("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    # Already-aliased ids pass through.
    assert _strip_snapshot_suffix("claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert _strip_snapshot_suffix("claude-haiku-4-5") == "claude-haiku-4-5"
    # Don't eat non-snapshot trailing segments.
    assert _strip_snapshot_suffix("claude-opus-4") == "claude-opus-4"
    # Unicode numeric lookalikes are not valid Anthropic snapshot aliases.
    assert _strip_snapshot_suffix("claude-haiku-4-5-２０２５１００１") == (
        "claude-haiku-4-5-２０２５１００１"
    )
    assert _strip_snapshot_suffix("claude-haiku-4-5-²²²²²²²²") == (
        "claude-haiku-4-5-²²²²²²²²"
    )
    assert _strip_snapshot_suffix("") == ""


def test_resolve_model_rates_matches_anthropic_published_pricing():
    # The Sonnet family must NOT resolve to Haiku rates — that was the bug.
    sonnet_in, sonnet_out = _resolve_model_rates("claude-sonnet-4-5-20250929")
    haiku_in, haiku_out = _resolve_model_rates("claude-haiku-4-5-20251001")
    assert (sonnet_in, sonnet_out) == _MODEL_RATES["claude-sonnet-4-5"]
    assert (haiku_in, haiku_out) == _MODEL_RATES["claude-haiku-4-5"]
    # Sonnet input rate is materially higher than Haiku input rate;
    # without this assertion the bug could come back via a typo.
    assert sonnet_in > haiku_in
    assert sonnet_out > haiku_out


def test_current_operational_default_models_have_exact_rates():
    defaults = Settings.model_fields
    default_claude = str(defaults["CLAUDE_MODEL"].default)
    effective_defaults = {
        "CLAUDE_MODEL": default_claude,
        "CLAUDE_SCORING_MODEL": default_claude,
        "CLAUDE_SCORING_BATCH_MODEL": str(
            defaults["CLAUDE_SCORING_BATCH_MODEL"].default
        ),
        "CLAUDE_CHAT_MODEL": str(defaults["CLAUDE_CHAT_MODEL"].default),
        "CLAUDE_AGENT_AUTONOMOUS_MODEL": default_claude,
        "CLAUDE_SEARCH_PARSER_MODEL": SONNET_MODEL,
        "CLAUDE_GROUNDING_MODEL": SONNET_MODEL,
        "GRAPHITI_LLM_MODEL": str(defaults["GRAPHITI_LLM_MODEL"].default),
        "GRAPHITI_LLM_SMALL_MODEL": str(
            defaults["GRAPHITI_LLM_SMALL_MODEL"].default
        ),
        "FAST_MODEL": FAST_MODEL,
        "SONNET_MODEL": SONNET_MODEL,
    }

    for source, model in effective_defaults.items():
        family = _strip_snapshot_suffix(model)
        assert family in _MODEL_RATES, f"{source} default is not exactly priced"
        assert is_priceable_claude_model(model), f"{source} is not outbound-enabled"
        assert _resolve_model_rates(model) == _MODEL_RATES[family]


def test_opus_4_5_uses_verified_current_rate_not_legacy_opus_rate():
    assert _resolve_model_rates("claude-opus-4-5-20251101") == (
        Decimal("5"),
        Decimal("25"),
    )
    assert _resolve_model_rates("claude-opus-4-20250514") == (
        Decimal("15"),
        Decimal("75"),
    )


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-3-haiku-20240307", (Decimal("0.25"), Decimal("1.25"))),
        ("claude-3-5-haiku-20241022", (Decimal("0.80"), Decimal("4"))),
        ("claude-3-7-sonnet-20250219", (Decimal("3"), Decimal("15"))),
        ("claude-sonnet-4-20250514", (Decimal("3"), Decimal("15"))),
        ("claude-opus-4-20250514", (Decimal("15"), Decimal("75"))),
        ("claude-opus-4-1-20250805", (Decimal("15"), Decimal("75"))),
    ],
)
def test_retired_models_remain_priceable_only_for_historical_usage(model, expected):
    assert _resolve_model_rates(model) == expected
    assert is_priceable_claude_model(model) is False
    with pytest.raises(UnpriceableClaudeModelError):
        require_priceable_claude_model(model)


@pytest.mark.parametrize(
    "model",
    [True, 7, " claude-haiku-4-5", "claude-haiku-4-5 ", ""],
)
def test_outbound_model_admission_does_not_coerce_identity(model):
    with pytest.raises(UnpriceableClaudeModelError):
        require_priceable_claude_model(model)


def test_raw_cost_matches_anthropic_for_real_sonnet_workload():
    """Pin: 2026-05-25 production Sonnet totals (4.0M input + 0.28M output
    + 12.7M cache_read + 1.9M cache_creation) must compute to ~$27, the
    actual Anthropic-reported number, NOT ~$9 (Haiku rates)."""
    cost_micro = raw_cost_usd_micro(
        input_tokens=4_004_618,
        output_tokens=283_379,
        cache_read_tokens=12_655_857,
        cache_creation_tokens=1_909_388,
        model="claude-sonnet-4-5-20250929",
    )
    # Expected: 4.005×3 + 0.283×15 + 12.656×0.30 + 1.909×3.75 = $27.22
    assert 27_000_000 < cost_micro < 28_000_000


def test_raw_cost_for_haiku_matches_haiku_rates():
    """Haiku rates stay where they were — same workload but routed via the
    Haiku family must produce ~⅓ of the Sonnet cost."""
    cost_micro = raw_cost_usd_micro(
        input_tokens=4_004_618,
        output_tokens=283_379,
        cache_read_tokens=12_655_857,
        cache_creation_tokens=1_909_388,
        model="claude-haiku-4-5-20251001",
    )
    # Expected: 4.005×1 + 0.283×5 + 12.656×0.10 + 1.909×1.25 = $9.07
    assert 8_900_000 < cost_micro < 9_300_000


def test_raw_cost_unknown_model_fails_closed_without_echoing_model():
    unknown = "claude-opus-99-untrusted-secret-marker"
    with pytest.raises(UnpriceableClaudeModelError) as error:
        raw_cost_usd_micro(
            input_tokens=1_000_000,
            output_tokens=0,
            model=unknown,
        )
    assert unknown not in str(error.value)


def test_raw_cost_without_model_uses_env_var_defaults():
    """Model-less historical calculations retain the legacy configured rate.

    Every non-empty unknown model is rejected instead of using this fallback.
    """
    cost_micro = raw_cost_usd_micro(
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert cost_micro > 0


def test_cache_creation_1h_priced_at_2x_input_rate():
    """1-hour cache writes bill at 2.00× input rate vs 1.25× for 5-minute.
    Pre-#387 the wrapper always assumed 1.25×; pre-screen / cv_match /
    agent prompts all use ttl=1h so this systematically under-counted.
    """
    # 1M cache_creation tokens, ALL 1h: 1M × $3 (Sonnet input) × 2.00 = $6.00
    all_1h = raw_cost_usd_micro(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=1_000_000,
        model="claude-sonnet-4-5",
    )
    assert 6_000_000 - 100 <= all_1h <= 6_000_000 + 100

    # 1M cache_creation tokens, ALL 5m: 1M × $3 × 1.25 = $3.75
    all_5m = raw_cost_usd_micro(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=0,
        model="claude-sonnet-4-5",
    )
    assert 3_750_000 - 100 <= all_5m <= 3_750_000 + 100

    # Mixed 50/50: 0.5M × 1.25 × $3 + 0.5M × 2.00 × $3 = 1.875 + 3.000 = $4.875
    mixed = raw_cost_usd_micro(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=500_000,
        model="claude-sonnet-4-5",
    )
    assert 4_875_000 - 100 <= mixed <= 4_875_000 + 100


def test_cache_creation_1h_none_falls_back_to_legacy_1_25x():
    """When ``cache_creation_1h_tokens`` is None (legacy row, older SDK),
    pricing falls back to applying 1.25× to the whole cache_creation
    total. This preserves pre-#387 behaviour exactly so we don't
    silently retroactively re-price historical rows the wrong way.
    """
    cost = raw_cost_usd_micro(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=None,
        model="claude-sonnet-4-5",
    )
    # 1M × $3 × 1.25 = $3.75 — the legacy under-counted answer.
    assert 3_750_000 - 100 <= cost <= 3_750_000 + 100


def test_malformed_cache_split_cannot_subtract_cost():
    malformed = raw_cost_usd_micro(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=2_000_000,
        model="claude-sonnet-4-5",
    )
    all_1h = raw_cost_usd_micro(
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
        cache_creation_1h_tokens=1_000_000,
        model="claude-sonnet-4-5",
    )
    assert malformed == all_1h == 6_000_000


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cache_creation_1h_tokens",
    ],
)
def test_negative_token_counts_are_rejected(field):
    kwargs = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_creation_1h_tokens": 0,
        "model": "claude-sonnet-4-5",
    }
    kwargs[field] = -1
    with pytest.raises(ValueError, match="non-negative"):
        raw_cost_usd_micro(**kwargs)
