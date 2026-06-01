"""Gap 2: per-org allocation of the shared-key Anthropic cost.

All prod spend is on the shared Anthropic key, so Anthropic only reports the
aggregate. ``allocate_reconciled_cost_by_org`` distributes the reconciled
Anthropic total across orgs by captured cost share, so per-org cost ties to the
Anthropic total exactly. Cache hits are excluded (no call ⇒ $0).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.models.anthropic_usage_reconciliation import AnthropicUsageReconciliation
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.anthropic_reconciliation_allocation import (
    allocate_reconciled_cost_by_org,
    reconciliation_factor,
)


def _ue(org_id, cost_micro, cache_hit=0):
    return UsageEvent(
        organization_id=org_id,
        feature="score",
        model="claude-haiku-4-5",
        input_tokens=0,
        output_tokens=0,
        cost_usd_micro=cost_micro,
        markup_multiplier=1,
        credits_charged=0,
        cache_hit=cache_hit,
        created_at=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
    )


def test_allocation_ties_to_anthropic_total_and_excludes_cache(db):
    # Reconciled window: Anthropic billed $10, internal captured $8 → factor 1.25.
    db.add(
        AnthropicUsageReconciliation(
            usage_date=date(2026, 5, 30),
            anthropic_workspace_id=None,
            organization_id=None,
            model="claude-haiku-4-5",
            anthropic_cost_usd_micro=10_000_000,
            internal_cost_usd_micro=8_000_000,
        )
    )
    org_a = Organization(name="A", slug=f"a-{id(db)}")
    org_b = Organization(name="B", slug=f"b-{id(db)}")
    db.add_all([org_a, org_b])
    db.flush()

    # Captured (non-cache) cost: A=$6, B=$2 (sum $8). Plus a $5 cache-hit on B
    # that must NOT count toward the share.
    db.add_all([
        _ue(org_a.id, 6_000_000),
        _ue(org_b.id, 2_000_000),
        _ue(org_b.id, 5_000_000, cache_hit=1),
    ])
    db.flush()

    factor = reconciliation_factor(db, start_date=date(2026, 5, 30), end_date=date(2026, 5, 30))
    assert abs(factor - 1.25) < 1e-9

    alloc = allocate_reconciled_cost_by_org(
        db, start_date=date(2026, 5, 30), end_date=date(2026, 5, 30)
    )
    # A: 6M × 1.25 = 7.5M ; B: 2M × 1.25 = 2.5M (cache-hit $5 excluded).
    assert alloc[org_a.id] == 7_500_000
    assert alloc[org_b.id] == 2_500_000
    # The invariant: allocated per-org totals sum to the Anthropic total.
    assert sum(alloc.values()) == 10_000_000


def test_factor_none_when_no_internal(db):
    assert reconciliation_factor(db, start_date=date(2026, 5, 1), end_date=date(2026, 5, 2)) is None
    assert allocate_reconciled_cost_by_org(db, start_date=date(2026, 5, 1), end_date=date(2026, 5, 2)) == {}
