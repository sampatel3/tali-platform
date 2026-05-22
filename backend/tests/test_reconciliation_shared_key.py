"""Reconciliation correctly attributes Default-workspace + alias-model spend.

Two distinct bugs found while debugging the 73% reconciliation gap on
2026-05-20:

1. **Default-workspace attribution.** Anthropic's admin API returns
   ``workspace_id=None`` for any call under the shared (non-workspace-
   scoped) API key — that's where 100% of the user's traffic lives,
   since no Tali org has its own provisioned workspace yet. The
   reconciliation matched ``Organization.anthropic_workspace_id == None``
   and found no rows (because no org has the column set), so internal
   was forced to 0 and drift showed -100% even when the meter was
   working correctly. Fix aggregates UsageEvent across every shared-
   key org instead.

2. **Model-alias mismatch.** Anthropic always returns the dated
   snapshot id (``claude-sonnet-4-5-20250929``), but the internal
   meter sometimes stores the short alias (``claude-sonnet-4-5``)
   because callers pass ``settings.resolved_claude_model`` which
   resolves to the alias. Exact-match join missed those rows — Sonnet
   4.5 reconciled to $0 even though $9 of it was metered. Fix matches
   on both the dated id and the date-stripped base.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.anthropic_reconciliation_service import (
    _aggregate_internal,
    _aggregate_internal_multi,
    _model_match_filter,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent


def _seed_event(
    db,
    *,
    org_id: int,
    model: str,
    cost_micro: int,
    when: datetime,
    feature: str = "score",
):
    db.add(
        UsageEvent(
            organization_id=org_id,
            feature=feature,
            model=model,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd_micro=cost_micro,
            markup_multiplier=1,
            credits_charged=cost_micro,
            cache_hit=0,
            created_at=when,
        )
    )
    db.flush()


def test_model_match_filter_accepts_dated_snapshot_and_short_alias(db):
    """``claude-sonnet-4-5-20250929`` from Anthropic should match
    ``claude-sonnet-4-5`` in our events (short alias) AND the dated id
    if the meter recorded it. Same query, both rows."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _seed_event(db, org_id=org.id, model="claude-sonnet-4-5", cost_micro=4_000_000, when=when)
    _seed_event(db, org_id=org.id, model="claude-sonnet-4-5-20250929", cost_micro=5_000_000, when=when)
    db.commit()

    result = _aggregate_internal(
        db,
        organization_id=int(org.id),
        model="claude-sonnet-4-5-20250929",
        usage_day=date(2026, 5, 20),
    )
    assert result["cost_usd_micro"] == 9_000_000
    assert result["event_count"] == 2


def test_model_match_filter_does_not_collapse_unrelated_models(db):
    """Sonnet 4.5 events shouldn't match Sonnet 4.6 even though they
    share a prefix. The date-stripped base is exact, not LIKE."""
    org = Organization(name="O2", slug=f"o2-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _seed_event(db, org_id=org.id, model="claude-sonnet-4-5", cost_micro=4_000_000, when=when)
    _seed_event(db, org_id=org.id, model="claude-sonnet-4-6", cost_micro=2_000_000, when=when)
    db.commit()

    result = _aggregate_internal(
        db,
        organization_id=int(org.id),
        model="claude-sonnet-4-6",
        usage_day=date(2026, 5, 20),
    )
    assert result["cost_usd_micro"] == 2_000_000
    assert result["event_count"] == 1


def test_aggregate_internal_multi_sums_across_shared_key_orgs(db):
    """When Anthropic returns ``workspace_id=None`` the reconciliation
    needs to sum every Tali org that uses the shared key. Anything
    short of that and 100% of shared-key spend shows as -100% drift."""
    org_a = Organization(name="A", slug=f"a-{id(db)}")
    org_b = Organization(name="B", slug=f"b-{id(db)}")
    db.add_all([org_a, org_b]); db.commit()
    when = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _seed_event(db, org_id=org_a.id, model="claude-haiku-4-5-20251001", cost_micro=3_000_000, when=when)
    _seed_event(db, org_id=org_b.id, model="claude-haiku-4-5-20251001", cost_micro=5_000_000, when=when)
    db.commit()

    result = _aggregate_internal_multi(
        db,
        organization_ids=[int(org_a.id), int(org_b.id)],
        model="claude-haiku-4-5-20251001",
        usage_day=date(2026, 5, 20),
    )
    assert result["cost_usd_micro"] == 8_000_000
    assert result["event_count"] == 2


def test_aggregate_internal_multi_with_empty_org_list_returns_zero(db):
    """No shared-key orgs → no aggregate. Don't crash; surface zero."""
    result = _aggregate_internal_multi(
        db,
        organization_ids=[],
        model="claude-haiku-4-5-20251001",
        usage_day=date(2026, 5, 20),
    )
    assert result["cost_usd_micro"] == 0
    assert result["event_count"] == 0
