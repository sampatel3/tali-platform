"""MeteredAsyncAnthropic — plugs the Graphiti metering bypass.

Until 2026-05-26 Graphiti's ``AnthropicClient`` built its own
``AsyncAnthropic`` and made entity-extraction calls that our sync
wrapper couldn't intercept. Symptom: 2026-05-23 Anthropic billed
19.18M Haiku input tokens; our claude_call_log captured 3.03M. The
missing 16M were all Graphiti's add_episode calls.

These tests pin the async wrapper's two invariants:
1. Every successful call writes a claude_call_log row with real
   tokens from response.usage.
2. When ``graph_metering_ctx`` is set, the wrapper ALSO writes a
   usage_event (feature=graph_sync) FK-linked to the call_log row.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.metered_async_anthropic_client import (
    GraphMeteringContext,
    MeteredAsyncAnthropic,
    graph_metering_ctx,
)


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _FakeResponse:
    usage: _FakeUsage
    id: str = "msg_test_001"


class _FakeAsyncMessages:
    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _FakeResponse(usage=self._usage)


class _FakeAsyncAnthropic:
    """Mimics the small slice of AsyncAnthropic the wrapper needs."""

    def __init__(self, *, usage: _FakeUsage):
        self.messages = _FakeAsyncMessages(usage=usage)


def _run(coro):
    # asyncio.get_event_loop() is deprecated when no loop exists; build
    # a fresh one per call so tests don't share state across functions.
    return asyncio.new_event_loop().run_until_complete(coro)


def test_create_writes_call_log_row_with_real_tokens(db):
    """Anthropic call succeeds → wrapper writes a ClaudeCallLog row with
    the exact tokens from response.usage. Without org context, the row
    still lands so reconciliation captures the spend (just unattributed).
    """
    inner = _FakeAsyncAnthropic(
        usage=_FakeUsage(
            input_tokens=15_234,
            output_tokens=1_842,
            cache_read_input_tokens=8_500,
            cache_creation_input_tokens=2_100,
        )
    )
    wrapped = MeteredAsyncAnthropic(inner=inner)

    resp = _run(wrapped.messages.create(model="claude-haiku-4-5-20251001", messages=[]))
    assert resp is not None

    # Use the in-test session to verify the row landed via SessionLocal.
    from app.platform.database import SessionLocal
    with SessionLocal() as s:
        rows = s.query(ClaudeCallLog).filter(
            ClaudeCallLog.feature_hint == "graph_sync",
            ClaudeCallLog.model == "claude-haiku-4-5-20251001",
        ).all()
        # The wrapper writes one row per call.
        assert len(rows) == 1
        row = rows[0]
        assert row.input_tokens == 15_234
        assert row.output_tokens == 1_842
        assert row.cache_read_tokens == 8_500
        assert row.cache_creation_tokens == 2_100
        # Cost is computed at Haiku rates (the per-model pricing fix
        # from the previous PR): 15234×1 + 1842×5 + 8500×0.10 + 2100×1.25
        # = 15.234 + 9.210 + 0.850 + 2.625 = 27.919 micro per token-µ unit
        # Actually we want micro-USD: (15234 + 1842×5 + 8500×0.1 + 2100×1.25)/1e6 USD
        # = (15234 + 9210 + 850 + 2625)/1e6 = 0.027919 USD → 27_919 micro
        assert 27_000 < row.cost_usd_micro < 29_000
        assert row.usage_event_id is None  # no org context → no usage_event
        # Clean up so other tests see an empty table.
        s.query(ClaudeCallLog).delete()
        s.commit()


def test_create_with_metering_ctx_links_usage_event(db):
    """When graph_metering_ctx is populated, the wrapper writes BOTH a
    claude_call_log row AND a FK-linked usage_event under feature=graph_sync.
    This is what makes the spend show up against the org's role budget.
    """
    org = Organization(name="O", slug=f"o-{id(db)}-ctx")
    db.add(org); db.commit()

    inner = _FakeAsyncAnthropic(
        usage=_FakeUsage(input_tokens=5_000, output_tokens=500)
    )
    wrapped = MeteredAsyncAnthropic(inner=inner)

    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            candidate_id=42,
            episode_name="profile_summary",
        )
    )
    try:
        _run(wrapped.messages.create(model="claude-haiku-4-5-20251001", messages=[]))
    finally:
        graph_metering_ctx.reset(token)

    from app.platform.database import SessionLocal
    with SessionLocal() as s:
        log_row = (
            s.query(ClaudeCallLog)
            .filter(ClaudeCallLog.feature_hint == "graph_sync")
            .one()
        )
        assert log_row.organization_id == int(org.id)
        assert log_row.usage_event_id is not None
        usage_row = s.query(UsageEvent).filter(UsageEvent.id == log_row.usage_event_id).one()
        assert usage_row.feature == "graph_sync"
        assert usage_row.organization_id == int(org.id)
        assert usage_row.entity_id == "42"
        assert usage_row.input_tokens == 5_000
        assert usage_row.output_tokens == 500
        # Clean up.
        s.query(ClaudeCallLog).delete()
        s.query(UsageEvent).delete()
        s.commit()


def test_create_failure_logs_sdk_error_row(db):
    """If the underlying call raises, the wrapper records an sdk_error row
    (tokens=0) and re-raises. We never swallow the exception."""

    class _Boom(_FakeAsyncMessages):
        async def create(self, **kwargs):
            raise RuntimeError("transient network blip")

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _Boom(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)

    with pytest.raises(RuntimeError, match="transient network blip"):
        _run(wrapped.messages.create(model="claude-haiku-4-5-20251001", messages=[]))

    from app.platform.database import SessionLocal
    with SessionLocal() as s:
        rows = s.query(ClaudeCallLog).filter(ClaudeCallLog.feature_hint == "graph_sync").all()
        assert len(rows) == 1
        assert rows[0].status == "sdk_error"
        assert rows[0].input_tokens == 0
        # Clean up so subsequent tests are isolated.
        s.query(ClaudeCallLog).delete()
        s.commit()
