"""Metering convergence shadow comparator (ADR-0010 cut #1b).

Behind a flag, every metered call is also priced through mainspring's vendored
seam and a parity diff is logged. These lock: no-op when off; the compared vs
unpriced statuses; and that it never raises (must not affect the live call).
"""
from __future__ import annotations

import logging

from app.platform.config import settings
from app.services.mainspring_metering_shadow import shadow_compare
from vendor.mainspring_metering.seam import TokenUsage, price_usage

_SHADOW_EVENTS = lambda caplog: [
    r for r in caplog.records if getattr(r, "event", None) == "mainspring_metering_shadow"
]


def test_vendored_seam_prices_a_known_dated_model():
    # mainspring's pricing keys on dated names; the vendored seam must price them.
    assert price_usage("claude-haiku-4-5-20251001", TokenUsage(input_tokens=1000, output_tokens=500)) > 0


def test_shadow_is_noop_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_METERING_SHADOW", False, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.metering.shadow"):
        shadow_compare(
            model="claude-haiku-4-5-20251001", tali_cost_usd_micro=1000,
            input_tokens=1000, output_tokens=500,
        )
    assert _SHADOW_EVENTS(caplog) == []


def test_shadow_logs_compared_with_drift(caplog, monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_METERING_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.metering.shadow"):
        shadow_compare(
            model="claude-haiku-4-5-20251001", tali_cost_usd_micro=1000,
            input_tokens=1000, output_tokens=500,
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "compared"
    assert evs[0].mainspring_micro > 0
    assert hasattr(evs[0], "drift_pct")


def test_shadow_flags_unpriced_when_mainspring_lacks_the_model(caplog, monkeypatch):
    """A model mainspring can't price (its PRICING/ALIASES table lacks it) logs
    as 'unpriced' — a mainspring pricing gap, not a misleading -100% drift.
    (tali's current aliases all price now after the pricing-parity cut, so this
    uses a deliberately-unknown model.)"""
    monkeypatch.setattr(settings, "MAINSPRING_METERING_SHADOW", True, raising=False)
    with caplog.at_level(logging.INFO, logger="taali.metering.shadow"):
        shadow_compare(
            model="claude-unknown-future-99", tali_cost_usd_micro=1000,
            input_tokens=1000, output_tokens=500,
        )
    evs = _SHADOW_EVENTS(caplog)
    assert evs and evs[0].status == "unpriced"


def test_shadow_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setattr(settings, "MAINSPRING_METERING_SHADOW", True, raising=False)
    # Garbage that would break pricing must be swallowed, never propagated.
    shadow_compare(model=None, tali_cost_usd_micro=0, input_tokens="x", output_tokens=None)
