"""Cache-hit usage_events must record ZERO Anthropic spend (the haiku
capture fix, 2026-05-31).

Root cause found in prod on 2026-05-31: the cv_score_orchestrator re-scored
the same open applications many times per day (sweeper/agent ticks). Each
re-score hit cv_score_cache — NO Anthropic call — yet wrote a fresh
``usage_event`` carrying the cached result's full token counts and full
``cost_usd_micro``. One application (50527) accrued 29 identical score
usage_events spanning 22 hours. claude_call_log (the reconciliation oracle)
correctly recorded NOTHING for those cache hits, so usage_events drifted far
above the oracle:

  * haiku score usage_events on 05-31: 2213 rows, output 17.3M
  * haiku score claude_call_log on 05-31:  866 rows, output  6.86M  (≈ Anthropic 7.68M)
  * of the 2213 usage_events, 1347 were cache_hit=1 orphans contributing
    10.46M phantom output tokens — the entire 153% output over-count.

Fix: ``record_event`` zeros the Anthropic-attributable columns (input/output/
cache tokens + cost_usd_micro) for cache hits, so usage_events agrees with
claude_call_log (no Anthropic call ⇒ no Anthropic spend). The customer-facing
``credits_charged`` cache fee (full cost × cache_hit_multiplier) is preserved
so unlimited free re-scoring is still discouraged, and budget_guard /
SUM(usage_events) reconciliation stop double-counting phantom spend.
"""
from __future__ import annotations

from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.pricing_service import (
    Feature,
    credits_charged,
    feature_pricing,
)


def _full_cost_micro(**kw):
    # The same seam record_event prices against — keep the expected-credits
    # math identical to production.
    from vendor.mainspring_metering.pricing import cost_for as _cost_for
    return _cost_for(**kw)


def test_cache_hit_records_zero_anthropic_tokens_and_cost(db):
    from app.services.usage_metering_service import record_event

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    # A score cache hit replays a cached result with real (cached) token
    # counts — exactly what cv_score_orchestrator passes from the cached
    # CVMatchOutput.
    in_tok, out_tok = 2122, 5367
    cc_tok = 7861
    ev = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.SCORE,
        model="claude-haiku-4-5-20251001",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=0,
        cache_creation_tokens=cc_tok,
        cache_hit=True,
        entity_id="application:50527",
    )
    db.commit()
    db.refresh(ev)

    # usage_events must agree with claude_call_log: no Anthropic call happened.
    assert ev.input_tokens == 0
    assert ev.output_tokens == 0
    assert ev.cache_read_tokens == 0
    assert ev.cache_creation_tokens == 0
    assert ev.cost_usd_micro == 0
    assert ev.cache_hit == 1

    # But the customer is still charged the cache fee = full cost × cache markup.
    full_cost = _full_cost_micro(
        model="claude-haiku-4-5-20251001",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=0,
        cache_creation_tokens=cc_tok,
        cache_creation_1h_tokens=None,
        service_tier="standard",
    )
    expected_credits = credits_charged(
        feature=Feature.SCORE, cost_usd_micro=full_cost, cache_hit=True
    )
    assert ev.credits_charged == expected_credits
    assert expected_credits > 0, "cache fee must be non-zero to deter free re-scoring"
    # And the markup persisted is the cache-hit multiplier, not the standard one.
    assert ev.markup_multiplier == feature_pricing(Feature.SCORE).cache_hit_multiplier


def test_cache_miss_still_records_full_anthropic_spend(db):
    """The fix must NOT touch the cache-MISS path: a real Anthropic call
    records full tokens + full cost (this is the row that ties 1:1 to
    claude_call_log)."""
    from app.services.usage_metering_service import record_event

    org = Organization(name="O2", slug=f"o2-{id(db)}")
    db.add(org); db.commit()

    in_tok, out_tok, cc_tok = 2122, 5367, 7861
    ev = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.SCORE,
        model="claude-haiku-4-5-20251001",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=0,
        cache_creation_tokens=cc_tok,
        cache_hit=False,
        entity_id="application:50527",
    )
    db.commit()
    db.refresh(ev)

    assert ev.input_tokens == in_tok
    assert ev.output_tokens == out_tok
    assert ev.cache_creation_tokens == cc_tok
    assert ev.cost_usd_micro > 0
    assert ev.cache_hit == 0


def test_many_cache_hits_do_not_inflate_summed_output_tokens(db):
    """Reproduce the prod symptom in miniature: 1 real call + N cache replays.
    SUM(output_tokens) over usage_events must equal the single real call's
    output — the N cache hits add ZERO Anthropic output, matching the oracle.
    """
    from sqlalchemy import func

    from app.services.usage_metering_service import record_event

    org = Organization(name="O3", slug=f"o3-{id(db)}")
    db.add(org); db.commit()

    out_tok = 5367
    # 1 real Anthropic call (cache miss) → records the real output.
    record_event(
        db, organization_id=int(org.id), feature=Feature.SCORE,
        model="claude-haiku-4-5-20251001",
        input_tokens=2122, output_tokens=out_tok, cache_creation_tokens=7861,
        cache_hit=False, entity_id="application:50527",
    )
    # 28 subsequent cache replays of the same cached result.
    for _ in range(28):
        record_event(
            db, organization_id=int(org.id), feature=Feature.SCORE,
            model="claude-haiku-4-5-20251001",
            input_tokens=2122, output_tokens=out_tok, cache_creation_tokens=7861,
            cache_hit=True, entity_id="application:50527",
        )
    db.commit()

    summed_out = db.query(func.coalesce(func.sum(UsageEvent.output_tokens), 0)).filter(
        UsageEvent.organization_id == org.id, UsageEvent.feature == "score",
    ).scalar()
    summed_cost = db.query(func.coalesce(func.sum(UsageEvent.cost_usd_micro), 0)).filter(
        UsageEvent.organization_id == org.id, UsageEvent.feature == "score",
    ).scalar()

    # Anthropic only saw one call: summed Anthropic output == that one call.
    assert summed_out == out_tok, (
        f"cache replays inflated summed output tokens: {summed_out} (expected {out_tok})"
    )
    # Customer was charged for all 29 (1 full + 28 cache fees) but the raw
    # Anthropic cost summed equals just the one real call.
    assert summed_cost > 0
    n_events = db.query(func.count(UsageEvent.id)).filter(
        UsageEvent.organization_id == org.id, UsageEvent.feature == "score",
    ).scalar()
    assert n_events == 29
