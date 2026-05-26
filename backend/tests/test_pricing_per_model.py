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


def test_raw_cost_unknown_model_falls_back_with_warning(caplog):
    """Unknown model logs a warning and falls back to env-var defaults so
    a model rev change doesn't silently break billing — but ops sees it."""
    with caplog.at_level("WARNING", logger="taali.pricing"):
        cost_micro = raw_cost_usd_micro(
            input_tokens=1_000_000,
            output_tokens=0,
            model="claude-future-model-99",
        )
    assert any("no rate table entry" in r.message for r in caplog.records)
    # Fallback path still returns a number — billing keeps running.
    assert cost_micro > 0


def test_raw_cost_without_model_uses_env_var_defaults():
    """Legacy callers that don't pass ``model`` still work — they get the
    env-var default rates with no warning (only NEW unknown models warn)."""
    cost_micro = raw_cost_usd_micro(
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert cost_micro > 0
