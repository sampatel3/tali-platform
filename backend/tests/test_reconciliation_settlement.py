"""Gap 3: batch-settlement-latency telemetry.

Re-reconciling a day re-reads our internal rows; the change since the last run
measures how long late spend keeps arriving. ``_settlement_delta_pct`` is the
pure decision function — flag a MATERIAL move on a day old enough to be settled,
on a material-spend day — so the telemetry is unit-testable without the Admin
API. A move at the window edge is what tells us the lookback is too narrow.
"""
from __future__ import annotations

from app.services import anthropic_reconciliation_service as svc


def test_material_late_movement_is_flagged():
    # 5-day-old, $10 Anthropic day whose internal jumped $1 → $9 on re-reconcile.
    delta = svc._settlement_delta_pct(
        prev_internal_micro=1_000_000,
        new_internal_micro=9_000_000,
        anthropic_cost_micro=10_000_000,
        age_days=5,
    )
    assert delta is not None and delta > 100


def test_fresh_day_not_flagged():
    # Below the settled-age threshold → expected to still be moving, no signal.
    assert (
        svc._settlement_delta_pct(
            prev_internal_micro=1_000_000,
            new_internal_micro=9_000_000,
            anthropic_cost_micro=10_000_000,
            age_days=1,
        )
        is None
    )


def test_immaterial_move_not_flagged():
    # 1% move is below _SETTLE_MATERIAL_PCT (2%).
    assert (
        svc._settlement_delta_pct(
            prev_internal_micro=1_000_000,
            new_internal_micro=1_010_000,
            anthropic_cost_micro=10_000_000,
            age_days=5,
        )
        is None
    )


def test_subdollar_day_not_flagged():
    # Sub-$1 Anthropic day → noise, never flagged.
    assert (
        svc._settlement_delta_pct(
            prev_internal_micro=1_000,
            new_internal_micro=9_000,
            anthropic_cost_micro=80_000,
            age_days=5,
        )
        is None
    )


def test_no_prior_internal_not_flagged():
    assert (
        svc._settlement_delta_pct(
            prev_internal_micro=0,
            new_internal_micro=9_000_000,
            anthropic_cost_micro=10_000_000,
            age_days=5,
        )
        is None
    )
