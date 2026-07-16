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
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.claude_call_log import ClaudeCallLog
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services.metered_async_anthropic_client import (
    GraphMeteringContext,
    GraphProviderAdmissionError,
    GraphUsageMeteringError,
    MeteredAsyncAnthropic,
    graph_metering_ctx,
)
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_PENDING_STATE,
)
from app.services.usage_credit_reservations import InsufficientRoleBudgetError
from app.services.usage_metering_service import InsufficientCreditsError


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


def _enable_live_holds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )


def _billed_role(db, *, balance: int = 100_000, cap_cents: int = 100):
    org = Organization(
        name="Graph Hold Org",
        slug=f"graph-hold-{id(db)}-{balance}-{cap_cents}",
        credits_balance=balance,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Graph Hold Role",
        monthly_usd_budget_cents=cap_cents,
    )
    db.add(role)
    db.commit()
    return org, role


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


def test_hard_admission_zero_org_credits_never_calls_anthropic(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=0)
    inner = _FakeAsyncAnthropic(usage=_FakeUsage(input_tokens=10, output_tokens=1))
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_id=42,
            episode_name="zero-credit-episode",
            trace_id="graph-outbox:zero-credit",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(InsufficientCreditsError):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert inner.messages.create_calls == []
    assert db.query(UsageEvent).count() == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_role_owned_graph_call_without_role_fails_before_anthropic(db):
    org = Organization(name="No Role Org", slug=f"no-role-{id(db)}")
    db.add(org)
    db.commit()
    inner = _FakeAsyncAnthropic(usage=_FakeUsage(input_tokens=10, output_tokens=1))
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            episode_name="missing-role",
            require_hard_admission=True,
            require_role_admission=True,
        )
    )
    try:
        with pytest.raises(GraphProviderAdmissionError, match="requires role"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert inner.messages.create_calls == []


def test_workspace_pause_after_first_call_blocks_next_autonomous_anthropic(
    db, monkeypatch,
):
    """One Graphiti episode may issue several LLM calls; re-admit each one."""

    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)
    role.agentic_mode_enabled = True
    db.commit()
    inner = _FakeAsyncAnthropic(
        usage=_FakeUsage(input_tokens=100, output_tokens=10)
    )
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="pause-between-anthropic-calls",
            require_hard_admission=True,
            require_role_admission=True,
        )
    )
    try:
        _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001", messages=[]
            )
        )
        org.agent_workspace_paused_at = datetime.now(timezone.utc)
        db.commit()

        with pytest.raises(GraphProviderAdmissionError, match="workspace agent is paused"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert len(inner.messages.create_calls) == 1


def test_paused_workspace_allows_explicit_workspace_anthropic_call(
    db, monkeypatch,
):
    """The global switch is an autonomy overlay, not a user-operation lock."""

    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Explicit Anthropic Workspace",
        slug=f"explicit-anthropic-{id(db)}",
        credits_balance=100_000,
        agent_workspace_paused_at=datetime.now(timezone.utc),
    )
    db.add(org)
    db.commit()
    inner = _FakeAsyncAnthropic(
        usage=_FakeUsage(input_tokens=100, output_tokens=10)
    )
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            episode_name="explicit-workspace-search",
            require_hard_admission=True,
            require_role_admission=False,
        )
    )
    try:
        _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001", messages=[]
            )
        )
    finally:
        graph_metering_ctx.reset(token)

    assert len(inner.messages.create_calls) == 1


def test_hard_admission_role_cap_never_calls_anthropic(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000, cap_cents=1)
    # Leave 9,999 microcredits under a 10,000-microcredit cap; GRAPH_SYNC's
    # committed hold is 10,000, so role admission must fail closed.
    db.add(
        UsageEvent(
            organization_id=int(org.id),
            role_id=int(role.id),
            feature="graph_sync",
            model="voyage-3",
            input_tokens=1,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd_micro=1,
            markup_multiplier=1,
            credits_charged=1,
            cache_hit=0,
        )
    )
    db.commit()
    inner = _FakeAsyncAnthropic(usage=_FakeUsage(input_tokens=10, output_tokens=1))
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="role-cap-episode",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(InsufficientRoleBudgetError):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert inner.messages.create_calls == []
    db.refresh(org)
    assert org.credits_balance == 100_000
    assert db.query(BillingCreditLedger).count() == 0


def test_hard_admission_settles_reserved_credits_to_actual_usage(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)
    inner = _FakeAsyncAnthropic(
        usage=_FakeUsage(input_tokens=1_000, output_tokens=100)
    )
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_id=77,
            episode_name="settled-episode",
            trace_id="graph-outbox:settle",
            require_hard_admission=True,
        )
    )
    try:
        _run(wrapped.messages.create(model="claude-haiku-4-5-20251001", messages=[]))
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    event = db.query(UsageEvent).one()
    refreshed_org = db.query(Organization).filter(Organization.id == org.id).one()
    assert event.role_id == int(role.id)
    assert event.entity_id == "77"
    assert event.event_metadata["provider"] == "anthropic"
    assert event.event_metadata["trace_id"] == "graph-outbox:settle"
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert refreshed_org.credits_balance == 100_000 - int(event.credits_charged)
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref.like("%:settled"))
        .count()
        == 1
    )


def test_hard_admission_ambiguous_provider_error_retains_hold(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)

    class _Boom(_FakeAsyncMessages):
        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            raise RuntimeError("anthropic temporarily unavailable")

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _Boom(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="provider-error-episode",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    assert db.query(Organization).filter(Organization.id == org.id).one().credits_balance == 90_000
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason.like("reservation:%"))
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .count()
        == 1
    )


def test_hard_admission_explicit_provider_rejection_releases_hold(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)

    class _Rejected(_FakeAsyncMessages):
        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            error = RuntimeError("invalid request")
            error.status_code = 400
            raise error

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _Rejected(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="provider-rejection-episode",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(RuntimeError, match="invalid request"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    assert db.get(Organization, int(org.id)).credits_balance == 100_000
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason.like("reservation_release:%"))
        .count()
        == 1
    )


def test_hard_admission_metering_error_keeps_hold_and_raises(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)
    inner = _FakeAsyncAnthropic(
        usage=_FakeUsage(input_tokens=1_000, output_tokens=100)
    )
    wrapped = MeteredAsyncAnthropic(inner=inner)

    def _metering_down(*args, **kwargs):
        raise RuntimeError("usage database unavailable")

    monkeypatch.setattr(
        "app.services.metered_async_anthropic_client.record_event",
        _metering_down,
    )
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="metering-error-episode",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(GraphUsageMeteringError, match="settlement failed"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    assert len(inner.messages.create_calls) == 1
    assert db.query(Organization).filter(Organization.id == org.id).one().credits_balance == 90_000
    assert db.query(UsageEvent).count() == 0
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason.like("reservation:%"))
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    assert hold.entry_metadata["deferred_usage_event"]["input_tokens"] == 1_000
    assert hold.entry_metadata["provider_request_id"] == "msg_test_001"
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref.like("%:settled"))
        .count()
        == 0
    )
    assert db.query(ClaudeCallLog).filter(ClaudeCallLog.status == "metering_error").count() == 1
