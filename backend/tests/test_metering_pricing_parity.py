"""Metering convergence: EXACT seam==brand pricing parity (ADR-0010).

The metering cutover prices recorded usage events through mainspring's vendored
``cost_for`` instead of tali's ``raw_cost_usd_micro``. That swap is only safe if
the two meters agree to the micro-credit on every call the brand bills — for
BOTH Anthropic service tiers:

* ``standard`` — the default tier.
* ``batch``    — the Message Batches API, billed at 50% of standard across all
  token categories (the CV-matching batch path). The brand halves the micro
  total and rounds UP; mainspring's ``cost_for`` must do the same.

This locks token-for-token parity across the brand's full ``_MODEL_RATES``
corpus, including the odd-token and cache (read / 5m-creation / 1h-creation)
scenarios that exercise the ROUND_UP rounding and the one fractional per-MTok
rate (claude-3-5-haiku at $0.80). A future drift in either meter — a rate
change vendored on only one side, or a rounding regression — fails here.
"""
from __future__ import annotations

import itertools

import pytest

from app.services.pricing_service import _MODEL_RATES, raw_cost_usd_micro
from vendor.mainspring_metering.pricing import cost_for as seam_cost_for

# Every model string the brand prices. The seam must price each identically.
_MODELS = sorted(_MODEL_RATES)

# Representative usages: zero, tiny/odd token counts, large counts, and every
# cache stream (read, 5m-creation, 1h-creation, mixed) — the cases that drive
# fractional micro-USD values and the ROUND_UP boundary.
_USAGES = [
    dict(input_tokens=0, output_tokens=0),
    dict(input_tokens=1, output_tokens=0),
    dict(input_tokens=1, output_tokens=1),
    dict(input_tokens=3, output_tokens=7),
    dict(input_tokens=1000, output_tokens=500),
    dict(input_tokens=1001, output_tokens=499),
    dict(input_tokens=12345, output_tokens=6789),
    dict(cache_read_tokens=1),
    dict(cache_read_tokens=3),
    dict(cache_read_tokens=7),
    dict(cache_read_tokens=15),
    dict(cache_creation_tokens=1),
    dict(cache_creation_tokens=3),
    dict(cache_creation_tokens=1000, cache_creation_1h_tokens=400),
    dict(cache_creation_tokens=1000, cache_creation_1h_tokens=1000),
    dict(cache_creation_tokens=777, cache_creation_1h_tokens=333),
    dict(input_tokens=1234, output_tokens=567, cache_read_tokens=89,
         cache_creation_tokens=43, cache_creation_1h_tokens=17),
    dict(input_tokens=99999, output_tokens=88888, cache_read_tokens=7777,
         cache_creation_tokens=333, cache_creation_1h_tokens=111),
    dict(input_tokens=5, output_tokens=5, cache_read_tokens=5),
    dict(input_tokens=2, output_tokens=0),
]

_TIERS = ("standard", "batch")


def _usage(u: dict) -> dict:
    # Fill the required input/output kwargs for cache-only scenarios.
    return {"input_tokens": 0, "output_tokens": 0, **u}


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("usage", _USAGES)
def test_seam_matches_brand_exactly(model, tier, usage):
    u = _usage(usage)
    tali = raw_cost_usd_micro(model=model, service_tier=tier, **u)
    seam = seam_cost_for(model=model, service_tier=tier, **u)
    assert seam == tali, (
        f"meter drift: model={model} tier={tier} usage={u} "
        f"tali={tali} seam={seam}"
    )


def test_full_corpus_is_100pct_exact():
    """Aggregate guard: every (model x usage x tier) cell is exact — the
    single assertion that proves 100% parity in one place."""
    total = mism = 0
    per_tier = {t: [0, 0] for t in _TIERS}
    for model, usage, tier in itertools.product(_MODELS, _USAGES, _TIERS):
        u = _usage(usage)
        tali = raw_cost_usd_micro(model=model, service_tier=tier, **u)
        seam = seam_cost_for(model=model, service_tier=tier, **u)
        total += 1
        per_tier[tier][1] += 1
        if tali == seam:
            per_tier[tier][0] += 1
        else:
            mism += 1
    assert mism == 0, f"{mism}/{total} cells drifted"
    # Both tiers must be fully covered (guards against an empty corpus).
    for tier in _TIERS:
        matched, count = per_tier[tier]
        assert matched == count and count == len(_MODELS) * len(_USAGES)


def test_batch_is_exactly_half_of_standard_when_even():
    """Sanity on the batch semantics itself: for a usage whose standard micro
    total is even, batch is exactly half (no rounding ambiguity)."""
    # 1000 input @ haiku $1/MTok = 1000 micro (even).
    std = raw_cost_usd_micro(model="claude-haiku-4-5", input_tokens=1000,
                             output_tokens=0, service_tier="standard")
    bat = seam_cost_for(model="claude-haiku-4-5", input_tokens=1000,
                        output_tokens=0, service_tier="batch")
    assert std == 1000
    assert bat == 500
    # And the brand agrees on the batch side too.
    assert raw_cost_usd_micro(model="claude-haiku-4-5", input_tokens=1000,
                              output_tokens=0, service_tier="batch") == 500


def test_service_tier_defaults_to_standard_on_seam():
    """The new param defaults to standard — omitting it must equal passing
    'standard' (zero behaviour change for existing callers)."""
    for model in _MODELS:
        u = dict(input_tokens=1234, output_tokens=567, cache_read_tokens=89,
                 cache_creation_tokens=43, cache_creation_1h_tokens=17)
        assert seam_cost_for(model=model, **u) == seam_cost_for(
            model=model, service_tier="standard", **u
        )


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": 0, "output_tokens": -1},
        {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": -1},
        {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": -1},
    ],
)
def test_both_pricers_reject_negative_token_counts(usage):
    with pytest.raises(ValueError, match="non-negative"):
        raw_cost_usd_micro(model="claude-haiku-4-5", **usage)
    with pytest.raises(ValueError, match="non-negative"):
        seam_cost_for(model="claude-haiku-4-5", **usage)


def test_both_pricers_clamp_out_of_range_one_hour_cache_slice():
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 100,
        "cache_creation_1h_tokens": 200,
    }
    expected = raw_cost_usd_micro(model="claude-haiku-4-5", **usage)
    assert seam_cost_for(model="claude-haiku-4-5", **usage) == expected


# ---------------------------------------------------------------------------
# Cutover assertion: record_event's BILLED cost is the seam's cost.
# ---------------------------------------------------------------------------
# After the cutover, ``record_event`` prices the raw Anthropic cost through the
# vendored seam (``cost_for``) — not tali's ``raw_cost_usd_micro``. These pin
# that the persisted ``UsageEvent.cost_usd_micro`` IS the seam's number (for
# both standard and batch tiers), and that the per-feature markup is layered on
# top of it unchanged. If a future change repoints record_event off the seam,
# or breaks the markup threading, this fails.

_CUTOVER_USAGES = [
    dict(input_tokens=1000, output_tokens=500),
    dict(input_tokens=1001, output_tokens=499, cache_read_tokens=89,
         cache_creation_tokens=43, cache_creation_1h_tokens=17),
    dict(input_tokens=12345, output_tokens=6789, cache_read_tokens=777),
]


@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("usage", _CUTOVER_USAGES)
def test_record_event_bills_the_seam_cost(db, tier, usage):
    from app.models.organization import Organization
    from app.services.pricing_service import Feature, credits_charged
    from app.services.usage_metering_service import record_event

    model = "claude-sonnet-4-5-20250929"
    org = Organization(name="O", slug=f"o-{tier}-{id(usage)}")
    db.add(org)
    db.commit()

    event = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.SCORE,
        model=model,
        service_tier=tier,
        **usage,
    )

    expected_raw = seam_cost_for(model=model, service_tier=tier, **usage)
    # The billed raw cost IS the seam's number — the cutover.
    assert event.cost_usd_micro == expected_raw
    # The per-feature markup (tali business logic) is layered on top, unchanged.
    assert event.credits_charged == credits_charged(
        feature=Feature.SCORE, cost_usd_micro=expected_raw, cache_hit=False
    )
