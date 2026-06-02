"""Per-role raw Anthropic cost vs margin (surfaced on the budget panel).

The agent budget cap is denominated in ``credits_charged`` (raw Anthropic cost
× per-feature markup). ``month_to_date_raw_cost_cents`` exposes the underlying
raw Anthropic cost over the same window, so the panel can show Anthropic cost
vs charged credits — and the margin between them — instead of only the
marked-up number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.agent_runtime import budget_guard
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent


def _seed(db, org_id, role_id, *, feature, raw_micro, charged_micro, markup, cache_hit=0):
    db.add(
        UsageEvent(
            organization_id=org_id,
            role_id=role_id,
            feature=feature,
            model="claude-haiku-4-5",
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd_micro=raw_micro,
            markup_multiplier=markup,
            credits_charged=charged_micro,
            cache_hit=cache_hit,
            created_at=datetime.now(timezone.utc),
        )
    )


def test_role_raw_cost_and_margin(db):
    org = Organization(name="MarginCo", slug=f"margin-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="Eng", source="manual", job_spec_text="x"
    )
    db.add(role)
    db.flush()

    # score @3x: raw $0.10 -> charged $0.30 ; agent @2x: raw $0.30 -> charged $0.60
    _seed(db, org.id, role.id, feature="score", raw_micro=100_000, charged_micro=300_000, markup=3)
    _seed(db, org.id, role.id, feature="agent_autonomous", raw_micro=300_000, charged_micro=600_000, markup=2)
    db.flush()

    raw_cents = budget_guard.month_to_date_raw_cost_cents(db, role=role)
    spent_cents = budget_guard.month_to_date_spend_cents(db, role=role)

    # raw = (100_000 + 300_000) micro / 10_000 = 40c ; charged = 900_000 / 10_000 = 90c
    assert raw_cents == 40
    assert spent_cents == 90
    # margin = charged - raw = 50c ; effective blended markup = 50/40 = 125%
    margin_cents = spent_cents - raw_cents
    assert margin_cents == 50
    assert round(margin_cents / raw_cents * 100, 1) == 125.0


def test_raw_cost_isolated_per_role(db):
    """Raw cost only counts the role's own events, like the charged sum."""
    org = Organization(name="IsoCo", slug=f"iso-{id(db)}")
    db.add(org)
    db.flush()
    role_a = Role(organization_id=org.id, name="A", source="manual", job_spec_text="x")
    role_b = Role(organization_id=org.id, name="B", source="manual", job_spec_text="x")
    db.add_all([role_a, role_b])
    db.flush()

    _seed(db, org.id, role_a.id, feature="score", raw_micro=500_000, charged_micro=1_500_000, markup=3)
    _seed(db, org.id, role_b.id, feature="score", raw_micro=200_000, charged_micro=600_000, markup=3)
    db.flush()

    assert budget_guard.month_to_date_raw_cost_cents(db, role=role_a) == 50
    assert budget_guard.month_to_date_raw_cost_cents(db, role=role_b) == 20


def test_org_spend_equals_sum_of_roles_and_excludes_null_role(db):
    """Consistency invariant: org MTD spend == Σ per-role cards, and neither
    counts role_id=NULL spend (graph_sync / unattributed). This is what makes a
    $50 job's usage align everywhere and org budget == sum of job cards."""
    org = Organization(name="ConsistencyCo", slug=f"cons-{id(db)}")
    db.add(org)
    db.flush()
    role_a = Role(organization_id=org.id, name="A", source="manual", job_spec_text="x")
    role_b = Role(organization_id=org.id, name="B", source="manual", job_spec_text="x")
    db.add_all([role_a, role_b])
    db.flush()

    _seed(db, org.id, role_a.id, feature="score", raw_micro=0, charged_micro=300_000, markup=3)
    _seed(db, org.id, role_b.id, feature="score", raw_micro=0, charged_micro=200_000, markup=3)
    # Unattributed (null-role) spend — must NOT count toward any role or the org cap.
    _seed(db, org.id, None, feature="graph_sync", raw_micro=0, charged_micro=500_000, markup=1)
    db.flush()

    a = budget_guard.month_to_date_spend_cents(db, role=role_a)
    b = budget_guard.month_to_date_spend_cents(db, role=role_b)
    by_role = budget_guard.spend_by_role_map(db, organization_id=org.id)
    org_total = budget_guard.org_month_to_date_spend_cents(db, organization_id=org.id)

    assert a == 30 and b == 20
    assert by_role == {role_a.id: 30, role_b.id: 20}  # null-role excluded
    assert org_total == 50  # excludes the $0.50 null-role event
    assert org_total == a + b == sum(by_role.values())  # org == Σ cards


def test_cache_hit_excluded_from_raw_cost_but_kept_in_credits(db):
    """A cache hit makes NO Anthropic call ⇒ $0 raw cost, even if a pre-#476
    row still carries the cached cost. The cache FEE stays in credits, so it is
    correctly counted as margin (not as Anthropic cost)."""
    org = Organization(name="CacheCo", slug=f"cache-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="C", source="manual", job_spec_text="x")
    db.add(role)
    db.flush()

    # Real call: raw $0.10 → charged $0.30.
    _seed(db, org.id, role.id, feature="score", raw_micro=100_000, charged_micro=300_000, markup=3)
    # Pre-#476 cache-hit row: phantom raw $0.50 but only a $0.05 cache fee.
    _seed(
        db, org.id, role.id, feature="score",
        raw_micro=500_000, charged_micro=50_000, markup=Decimal("0.10"), cache_hit=1,
    )
    db.flush()

    raw_cents = budget_guard.month_to_date_raw_cost_cents(db, role=role)
    spent_cents = budget_guard.month_to_date_spend_cents(db, role=role)

    assert raw_cents == 10  # excludes the cache-hit phantom; only the real $0.10
    assert spent_cents == 35  # credits include the cache fee: (300_000+50_000)/10_000
    assert spent_cents - raw_cents == 25  # cache fee correctly counted as margin
