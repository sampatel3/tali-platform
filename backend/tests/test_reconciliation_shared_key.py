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
)
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent


def _seed_event(
    db,
    *,
    org_id: int,
    model: str,
    cost_micro: int,
    when: datetime,
    feature: str = "score",
    cache_hit: int = 0,
):
    ev = UsageEvent(
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
        cache_hit=cache_hit,
        created_at=when,
    )
    db.add(ev)
    db.flush()
    return ev


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


# --------------------------------------------------------------------------- #
# claude_call_log as source of truth (2026-05-22)                             #
# --------------------------------------------------------------------------- #
#
# The reconciliation now prefers claude_call_log (every Anthropic call writes
# a row, unconditional) over usage_events, falling back to usage_events only
# for days where the call log has zero rows (pre-#237 history).

def _seed_call_log(db, *, org_id, model, cost_micro, when, status="ok", usage_event_id=None):
    row = ClaudeCallLog(
        organization_id=org_id,
        model=model,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd_micro=cost_micro,
        feature_hint="score",
        status=status,
        created_at=when,
        usage_event_id=usage_event_id,
    )
    db.add(row)
    db.flush()
    return row


def test_dedupes_usage_events_linked_to_call_log(db):
    """A usage_event already represented by a call_log row (via
    usage_event_id) must not be double-counted on top of call_log."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
    # 5 calls, each wrote BOTH a usage_event ($2) and a linked call_log row ($2).
    for _ in range(5):
        ev = _seed_event(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=2_000_000, when=when)
        _seed_call_log(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=2_000_000, when=when, usage_event_id=int(ev.id))
    db.commit()

    agg = _aggregate_internal(db, organization_id=int(org.id), model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 22))
    assert agg["cost_usd_micro"] == 10_000_000  # call_log only — linked usage_events deduped
    assert agg["event_count"] == 5


def test_combines_unlinked_usage_events_on_partial_day(db):
    """Partial coverage: some calls wrote only a usage_event (no call_log
    row). Those must be ADDED to call_log totals, not dropped."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
    # 4 calls via the metered client: usage_event ($2) + linked call_log ($2).
    for _ in range(4):
        ev = _seed_event(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=2_000_000, when=when)
        _seed_call_log(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=2_000_000, when=when, usage_event_id=int(ev.id))
    # 1 call metered via record_event only — no call_log row, unlinked ($3).
    _seed_event(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=3_000_000, when=when)
    db.commit()

    agg = _aggregate_internal(db, organization_id=int(org.id), model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 22))
    assert agg["cost_usd_micro"] == 11_000_000  # $8 call_log + $3 unlinked usage_event
    assert agg["event_count"] == 5  # 4 call_log + 1 unlinked usage_event


def test_falls_back_to_usage_events_when_call_log_empty(db):
    """Pre-#237 day: no call_log rows → fall back to usage_events so
    historical reconciliation still has an internal number."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)  # before call_log existed
    _seed_event(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=7_000_000, when=when)
    db.commit()

    agg = _aggregate_internal(db, organization_id=int(org.id), model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 19))
    assert agg["cost_usd_micro"] == 7_000_000  # usage_events fallback
    assert agg["event_count"] == 1


def test_excludes_cache_hit_usage_events_from_internal_cost(db):
    """cache_hit=1 usage_events represent a CACHE replay — cv_score_orchestrator
    charges the customer for the cached result but Anthropic was never called.
    Including them in the internal-vs-Anthropic comparison inflates internal
    spend with money Anthropic never billed. The 2026-05-25 prod symptom was
    +138% Haiku drift driven by 733 cache-hit usage_events at $37.76.
    """
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    # 3 real Anthropic calls → call_log rows ($2 each).
    for _ in range(3):
        ev = _seed_event(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=2_000_000, when=when)
        _seed_call_log(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=2_000_000, when=when, usage_event_id=int(ev.id))
    # 5 cache-HIT usage_events ($5 each) — NO call_log row, charged at retail.
    for _ in range(5):
        _seed_event(db, org_id=org.id, model="claude-haiku-4-5-20251001", cost_micro=5_000_000, when=when, cache_hit=1)
    db.commit()

    agg = _aggregate_internal(db, organization_id=int(org.id), model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 25))
    assert agg["cost_usd_micro"] == 6_000_000  # only the 3 call_log rows; cache hits excluded
    assert agg["event_count"] == 3


def test_shared_key_aggregate_includes_null_org_call_log(db):
    """Default-workspace (shared-key) bucket must count calls Tali couldn't
    attribute to an org. Graphiti's async wrapper writes claude_call_log with
    organization_id=None when its metering contextvar isn't set; Anthropic
    still bills those to the Default workspace. An ``IN (...)`` org predicate
    never matches NULL, so without ``include_null_org`` the shared-key
    aggregate silently dropped ~13.5M Haiku input tokens/day — the dominant
    cause of the −28%..−46% Haiku reconciliation drift on 2026-05-31.
    """
    org_a = Organization(name="SA", slug=f"sa-{id(db)}")
    db.add(org_a); db.commit()
    when = datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)
    # Attributed shared-key spend: $4 of org_a call_log.
    _seed_call_log(db, org_id=int(org_a.id), model="claude-haiku-4-5-20251001", cost_micro=4_000_000, when=when)
    # Unattributed shared-key spend: $6 of graph_sync with NULL org.
    _seed_call_log(db, org_id=None, model="claude-haiku-4-5-20251001", cost_micro=6_000_000, when=when)
    db.commit()

    # Without include_null_org (workspace-scoped org path): NULL-org dropped.
    scoped = _aggregate_internal_multi(
        db, organization_ids=[int(org_a.id)],
        model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 31),
    )
    assert scoped["cost_usd_micro"] == 4_000_000
    assert scoped["event_count"] == 1

    # Shared-key bucket sets include_null_org=True: NULL-org spend counted.
    shared = _aggregate_internal_multi(
        db, organization_ids=[int(org_a.id)],
        model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 31),
        include_null_org=True,
    )
    assert shared["cost_usd_micro"] == 10_000_000  # $4 attributed + $6 NULL-org
    assert shared["event_count"] == 2


def test_shared_key_null_org_does_not_double_count_linked_events(db):
    """A NULL-org call_log row that FK-links a usage_event must not be added
    twice in the shared-key bucket: the call_log already represents the spend,
    and the linked usage_event is deduped via the (now NULL-org-aware) linked
    subquery."""
    org = Organization(name="SB", slug=f"sb-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 31, 11, 0, tzinfo=timezone.utc)
    # NULL-org graph_sync call that DID get a usage_event (contextvar was set
    # on this one): usage_event carries the org, call_log is NULL-org & linked.
    ev = _seed_event(db, org_id=int(org.id), model="claude-haiku-4-5-20251001", cost_micro=5_000_000, when=when, feature="graph_sync")
    _seed_call_log(db, org_id=None, model="claude-haiku-4-5-20251001", cost_micro=5_000_000, when=when, usage_event_id=int(ev.id))
    db.commit()

    shared = _aggregate_internal_multi(
        db, organization_ids=[int(org.id)],
        model="claude-haiku-4-5-20251001", usage_day=date(2026, 5, 31),
        include_null_org=True,
    )
    # Just the one call: counted once via call_log, the linked usage_event deduped.
    assert shared["cost_usd_micro"] == 5_000_000
    assert shared["event_count"] == 1


def test_excludes_agent_autonomous_unlinked_usage_events(db):
    """The agent orchestrator passes ``metering={"skip": True}`` to the
    wrapper (so the wrapper writes ONLY a call_log row) and then writes
    its own usage_event via record_event for richer attribution. Both
    rows represent the SAME Anthropic call. The old reconciliation
    counted both, producing 2× drift on the agent path (e.g. 2026-05-25
    Sonnet: $9.07 call_log + $9.07 unlinked agent_autonomous = $18.15).
    """
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()
    when = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    # 4 agent calls: each writes a call_log (skip path) AND an unlinked
    # agent_autonomous usage_event. Same call, two rows.
    for _ in range(4):
        _seed_event(db, org_id=org.id, model="claude-sonnet-4-5", cost_micro=3_000_000, when=when, feature="agent_autonomous")
        _seed_call_log(db, org_id=org.id, model="claude-sonnet-4-5", cost_micro=3_000_000, when=when, usage_event_id=None)
    db.commit()

    agg = _aggregate_internal(db, organization_id=int(org.id), model="claude-sonnet-4-5", usage_day=date(2026, 5, 25))
    # call_log only: 4 × $3 = $12. agent_autonomous unlinked usage_events
    # are excluded so we don't double-count.
    assert agg["cost_usd_micro"] == 12_000_000
    assert agg["event_count"] == 4
