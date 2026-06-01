"""MeteredVoyageClient — captures Voyage embedding spend (Graphiti vector layer).

Anthropic has no embeddings API; Graphiti uses Voyage. Those calls were
previously invisible to billing + the org budget. These tests pin:
1. voyage_cost_micro prices voyage-3 at $0.06/1M tokens (input only).
2. record_event routes voyage models through the Voyage rate table, not the
   Anthropic seam.
3. The embed() wrapper writes a call_log row (always) and a FK-linked
   usage_event (feature=graph_sync, model=voyage-*) when graph_metering_ctx
   is set.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.metered_async_anthropic_client import (
    GraphMeteringContext,
    graph_metering_ctx,
)
from app.services.metered_voyage_embedder import MeteredVoyageClient
from app.services.pricing_service import (
    Feature,
    is_voyage_model,
    voyage_cost_micro,
)
from app.services.usage_metering_service import record_event


class _FakeEmbeddingsObject:
    def __init__(self, total_tokens: int):
        self.embeddings = [[0.1, 0.2, 0.3]]
        self.total_tokens = total_tokens


class _FakeVoyageClient:
    def __init__(self, total_tokens: int):
        self._total_tokens = total_tokens
        self.calls: list[dict[str, Any]] = []

    async def embed(self, texts, model=None, **kwargs):
        self.calls.append({"texts": texts, "model": model})
        return _FakeEmbeddingsObject(self._total_tokens)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------


def test_voyage_cost_micro_prices_voyage_3():
    assert is_voyage_model("voyage-3") is True
    assert is_voyage_model("claude-haiku-4-5") is False
    # $0.06 / 1M tokens == 0.06 micro-USD / token.
    assert voyage_cost_micro(model="voyage-3", input_tokens=1_000_000) == 60_000
    assert voyage_cost_micro(model="voyage-3", input_tokens=100_000) == 6_000
    # unknown voyage model falls back to the voyage-3 rate, not an Anthropic one.
    assert voyage_cost_micro(model="voyage-9-future", input_tokens=1_000_000) == 60_000


def test_record_event_prices_voyage_via_voyage_table(db):
    """A voyage model must NOT be priced via the Anthropic seam (which would
    book Haiku/Sonnet rates or fall through to env defaults)."""
    org = Organization(name="O", slug=f"o-{id(db)}-voy-rate")
    db.add(org)
    db.commit()
    ev = record_event(
        db,
        organization_id=int(org.id),
        feature=Feature.GRAPH_SYNC,
        model="voyage-3",
        input_tokens=500_000,
        output_tokens=0,
    )
    db.flush()
    # 500k * 0.06 = 30_000 micro = $0.03 — the Voyage rate, not Anthropic's.
    assert ev.cost_usd_micro == 30_000
    assert ev.model == "voyage-3"


# --------------------------------------------------------------------------
# Wire-tap
# --------------------------------------------------------------------------


def test_embed_without_ctx_writes_call_log_only(db):
    wrapped = MeteredVoyageClient(_FakeVoyageClient(total_tokens=12_000))
    result = _run(wrapped.embed(["hello", "world"], model="voyage-3"))
    assert result.embeddings  # passthrough unchanged

    from app.platform.database import SessionLocal

    with SessionLocal() as s:
        rows = s.query(ClaudeCallLog).filter(ClaudeCallLog.model == "voyage-3").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.input_tokens == 12_000
        assert row.output_tokens == 0
        assert row.cost_usd_micro == 720  # 12_000 * 0.06
        assert row.feature_hint == "graph_sync"
        assert row.usage_event_id is None  # no org context → no usage_event
        assert row.organization_id is None
        s.query(ClaudeCallLog).delete()
        s.commit()


def test_embed_with_ctx_links_usage_event(db):
    org = Organization(name="O", slug=f"o-{id(db)}-voy-ctx")
    db.add(org)
    db.commit()

    wrapped = MeteredVoyageClient(_FakeVoyageClient(total_tokens=200_000))
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            candidate_id=7,
            role_id=3,
            episode_name="candidate_profile",
        )
    )
    try:
        _run(wrapped.embed(["a", "b", "c"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    from app.platform.database import SessionLocal

    with SessionLocal() as s:
        ue = (
            s.query(UsageEvent)
            .filter(UsageEvent.model == "voyage-3", UsageEvent.feature == "graph_sync")
            .all()
        )
        assert len(ue) == 1
        event = ue[0]
        assert event.organization_id == int(org.id)
        assert event.input_tokens == 200_000
        assert event.cost_usd_micro == 12_000  # 200_000 * 0.06
        assert event.credits_charged > 0  # flows into the org budget

        cl = s.query(ClaudeCallLog).filter(ClaudeCallLog.model == "voyage-3").all()
        assert len(cl) == 1
        assert cl[0].usage_event_id == int(event.id)  # FK-linked oracle row
        assert cl[0].organization_id == int(org.id)

        s.query(ClaudeCallLog).delete()
        s.query(UsageEvent).delete()
        s.commit()
