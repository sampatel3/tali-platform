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
from types import SimpleNamespace
from typing import Any

import pytest
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.prompts.models import Message

from app.models.claude_call_log import ClaudeCallLog
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services import metered_async_anthropic_client as async_anthropic_module
from app.services.claude_model_pricing import UnpriceableClaudeModelError
from app.services.metered_async_anthropic_client import (
    GraphMeteringContext,
    GraphProviderAdmissionError,
    MeteredAsyncAnthropic,
    graph_metering_ctx,
)
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
)
from app.services import provider_retry_policy
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
    usage: _FakeUsage | None
    id: str = "msg_test_001"


class _FakeAsyncMessages:
    def __init__(self, *, usage: _FakeUsage | None):
        self._usage = usage
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _FakeResponse(usage=self._usage)

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return object()


class _FakeAsyncAnthropic:
    """Mimics the small slice of AsyncAnthropic the wrapper needs."""

    def __init__(self, *, usage: _FakeUsage | None):
        self.messages = _FakeAsyncMessages(usage=usage)


def _run(coro):
    # Build and close a fresh loop per call so tests do not share async state.
    return asyncio.run(coro)


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


def test_unpriceable_async_create_precedes_reservation_and_provider(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db)
    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    unknown = "claude-opus-99-untrusted-secret-marker"
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            require_hard_admission=True,
            require_role_admission=True,
        )
    )
    try:
        with pytest.raises(UnpriceableClaudeModelError) as error:
            _run(wrapped.messages.create(model=unknown, messages=[]))
    finally:
        graph_metering_ctx.reset(token)

    assert unknown not in str(error.value)
    assert inner.messages.create_calls == []
    assert db.query(BillingCreditLedger).count() == 0
    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0


def test_unpriceable_async_stream_is_blocked_before_provider(db):
    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    unknown = "claude-opus-99-untrusted-secret-marker"

    with pytest.raises(UnpriceableClaudeModelError) as error:
        wrapped.messages.stream(model=unknown, messages=[])

    assert unknown not in str(error.value)
    assert inner.messages.stream_calls == []
    assert db.query(BillingCreditLedger).count() == 0
    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0


def test_create_without_context_fails_before_provider(db):
    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)

    with pytest.raises(GraphProviderAdmissionError, match="organization metering"):
        _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[],
            )
        )
    assert inner.messages.create_calls == []
    assert db.query(BillingCreditLedger).count() == 0
    assert db.query(ClaudeCallLog).count() == 0


@pytest.mark.parametrize("organization_id", [0, -1, True, False, 1.5, "1"])
def test_invalid_context_organization_never_calls_provider(db, organization_id):
    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(organization_id=organization_id)
    )
    try:
        with pytest.raises(GraphProviderAdmissionError, match="positive organization"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[],
                )
            )
    finally:
        graph_metering_ctx.reset(token)
    assert inner.messages.create_calls == []


@pytest.mark.parametrize("role_id", [0, -1, True, False, 1.5, "1"])
def test_invalid_context_role_never_calls_provider(db, role_id):
    org = Organization(name="Invalid role ctx", slug=f"invalid-role-{id(db)}-{role_id}")
    db.add(org)
    db.commit()
    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(organization_id=int(org.id), role_id=role_id)
    )
    try:
        with pytest.raises(GraphProviderAdmissionError, match="positive integer"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[],
                )
            )
    finally:
        graph_metering_ctx.reset(token)
    assert inner.messages.create_calls == []


@pytest.mark.parametrize("field", ["user_id", "candidate_id"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "1"])
def test_invalid_optional_context_identity_never_calls_provider(
    db,
    field,
    value,
):
    org = Organization(
        name="Invalid optional ctx",
        slug=f"invalid-optional-{id(db)}-{field}-{value}",
    )
    db.add(org)
    db.commit()
    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    context = {"organization_id": int(org.id), field: value}
    token = graph_metering_ctx.set(GraphMeteringContext(**context))
    try:
        with pytest.raises(GraphProviderAdmissionError, match="positive integer"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[],
                )
            )
    finally:
        graph_metering_ctx.reset(token)
    assert inner.messages.create_calls == []


def test_graph_outbox_marker_runs_immediately_before_anthropic_sdk(db):
    order: list[str] = []
    org = Organization(name="Marker org", slug=f"marker-{id(db)}")
    db.add(org)
    db.commit()

    class _OrderedMessages(_FakeAsyncMessages):
        async def create(self, **kwargs):
            order.append("sdk")
            return await super().create(**kwargs)

    inner = _FakeAsyncAnthropic(usage=_FakeUsage(input_tokens=1, output_tokens=1))
    inner.messages = _OrderedMessages(usage=_FakeUsage(input_tokens=1, output_tokens=1))
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            provider_attempt_callback=lambda: order.append("marker") or True,
        )
    )
    try:
        _run(wrapped.messages.create(model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]))
    finally:
        graph_metering_ctx.reset(token)

    assert order == ["marker", "sdk"]


def test_failed_graph_outbox_marker_blocks_anthropic_sdk(db):
    org = Organization(name="Blocked marker org", slug=f"blocked-marker-{id(db)}")
    db.add(org)
    db.commit()
    inner = _FakeAsyncAnthropic(usage=_FakeUsage(input_tokens=1, output_tokens=1))
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            provider_attempt_callback=lambda: False,
        )
    )
    try:
        with pytest.raises(GraphProviderAdmissionError, match="graph-ingest attempt"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert inner.messages.create_calls == []


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
        _run(wrapped.messages.create(model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]))
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


def test_create_failure_logs_ambiguous_sdk_error_row(db):
    """An unknown post-invocation failure retains an ambiguous evidence row."""

    class _Boom(_FakeAsyncMessages):
        async def create(self, **kwargs):
            raise RuntimeError("transient network blip")

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _Boom(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    org = Organization(name="Failure org", slug=f"failure-{id(db)}")
    db.add(org)
    db.commit()
    token = graph_metering_ctx.set(
        GraphMeteringContext(organization_id=int(org.id))
    )
    try:
        with pytest.raises(RuntimeError, match="transient network blip"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[],
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    from app.platform.database import SessionLocal
    with SessionLocal() as s:
        rows = s.query(ClaudeCallLog).filter(ClaudeCallLog.feature_hint == "graph_sync").all()
        assert len(rows) == 1
        assert rows[0].status == "sdk_ambiguous_error"
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
                    model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
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
                    model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
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
                model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
            )
        )
        org.agent_workspace_paused_at = datetime.now(timezone.utc)
        db.commit()

        with pytest.raises(GraphProviderAdmissionError, match="workspace agent is paused"):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
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
                model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
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
                    model="claude-haiku-4-5-20251001", max_tokens=4_096, messages=[]
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
        _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[],
            )
        )
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
                    model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason.like("reservation:%"))
        .one()
    )
    assert (
        db.query(Organization).filter(Organization.id == org.id).one().credits_balance
        == 100_000 + int(hold.delta)
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .count()
        == 1
    )


def test_timeout_retry_has_fresh_graph_hold_and_attempt_log(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)

    async def _no_wait(**_kwargs):
        return None

    monkeypatch.setattr(
        provider_retry_policy,
        "async_sleep_before_retry",
        _no_wait,
    )

    class _TimeoutThenSuccess(_FakeAsyncMessages):
        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            if len(self.create_calls) == 1:
                raise TimeoutError("first Graphiti response timed out")
            return _FakeResponse(usage=self._usage, id="msg_retry_success")

    inner = _FakeAsyncAnthropic(usage=_FakeUsage(input_tokens=25, output_tokens=5))
    inner.messages = _TimeoutThenSuccess(usage=_FakeUsage(input_tokens=25, output_tokens=5))
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_id=42,
            trace_id="graph-timeout-retry",
            require_hard_admission=True,
        )
    )
    try:
        response = _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[],
            )
        )
    finally:
        graph_metering_ctx.reset(token)

    assert response.id == "msg_retry_success"
    assert len(inner.messages.create_calls) == 2
    db.expire_all()
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .order_by(BillingCreditLedger.id.asc())
        .all()
    )
    assert len(holds) == 2
    assert holds[0].external_ref != holds[1].external_ref
    assert (
        holds[0].entry_metadata["reservation_request_sha256"]
        == holds[1].entry_metadata["reservation_request_sha256"]
    )
    assert holds[0].entry_metadata["state"] == "provider_attempt_started"
    logs = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.organization_id == int(org.id))
        .order_by(ClaudeCallLog.id.asc())
        .all()
    )
    assert [(row.status, row.retry_attempt) for row in logs] == [
        ("sdk_ambiguous_error", 0),
        ("ok", 1),
    ]
    assert logs[1].parent_call_log_id == logs[0].id
    assert logs[0].error_reason == "anthropic_create:TimeoutError"
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 1


def test_cancelled_graph_call_retains_hold_and_writes_ambiguous_evidence(
    db, monkeypatch, caplog,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)
    secret_marker = "cancelled-anthropic-secret-must-not-escape"

    class _Cancelled(_FakeAsyncMessages):
        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            raise asyncio.CancelledError(secret_marker)

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _Cancelled(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="graph-cancelled",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[],
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    assert len(inner.messages.create_calls) == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
    evidence = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.organization_id == int(org.id))
        .one()
    )
    assert evidence.status == "sdk_ambiguous_error"
    assert evidence.error_reason == "anthropic_create:CancelledError"
    assert secret_marker not in caplog.text


def test_graph_retry_stops_when_failure_evidence_cannot_persist(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)

    async def _no_wait(**_kwargs):
        return None

    monkeypatch.setattr(provider_retry_policy, "async_sleep_before_retry", _no_wait)
    monkeypatch.setattr(
        async_anthropic_module,
        "record_async_anthropic_call_log",
        lambda **_kwargs: None,
    )

    class _Timeout(_FakeAsyncMessages):
        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            raise TimeoutError("provider result uncertain")

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _Timeout(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="graph-evidence-down",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(TimeoutError):
            _run(
                wrapped.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    messages=[],
                )
            )
    finally:
        graph_metering_ctx.reset(token)

    assert len(inner.messages.create_calls) == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"


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
                    model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
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


def test_hard_admission_metering_error_keeps_hold_and_returns_response(
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
        response = _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=100, messages=[]
            )
        )
    finally:
        graph_metering_ctx.reset(token)

    assert response.id == "msg_test_001"
    db.expire_all()
    assert len(inner.messages.create_calls) == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason.like("reservation:%"))
        .one()
    )
    assert (
        db.query(Organization).filter(Organization.id == org.id).one().credits_balance
        == 100_000 + int(hold.delta)
    )
    assert db.query(UsageEvent).count() == 0
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


def test_hard_admission_missing_usage_keeps_unknown_hold_and_returns_response(
    db,
    monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=100_000)
    inner = _FakeAsyncAnthropic(usage=None)
    wrapped = MeteredAsyncAnthropic(inner=inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="missing-usage-episode",
            require_hard_admission=True,
        )
    )
    try:
        response = _run(
            wrapped.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[],
            )
        )
    finally:
        graph_metering_ctx.reset(token)

    assert response.id == "msg_test_001"
    assert len(inner.messages.create_calls) == 1
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    assert hold.entry_metadata["deferred_usage_event"] is None
    assert db.query(UsageEvent).count() == 0
    log = db.query(ClaudeCallLog).one()
    assert log.status == "no_usage_on_response"
    assert log.usage_event_id is None


@pytest.mark.parametrize(
    ("usage", "settlement_fails", "expected_state", "expected_status"),
    [
        (
            _FakeUsage(input_tokens=1_000, output_tokens=100),
            True,
            PROVIDER_SUCCEEDED_PENDING_STATE,
            "metering_error",
        ),
        (None, False, PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE, "no_usage_on_response"),
    ],
    ids=["deferred-settlement", "usage-absent"],
)
def test_graphiti_does_not_replay_anthropic_success_after_metering_gap(
    db,
    monkeypatch,
    usage,
    settlement_fails,
    expected_state,
    expected_status,
):
    """Pinned Graphiti must receive the first paid response, not retry it."""

    _enable_live_holds(monkeypatch)
    org, role = _billed_role(db, balance=1_000_000)

    class _GraphitiMessages(_FakeAsyncMessages):
        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            return SimpleNamespace(
                id="msg_graphiti_settlement_gap",
                usage=usage,
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        input={"accepted": True},
                    )
                ],
            )

    inner = _FakeAsyncAnthropic(usage=_FakeUsage())
    inner.messages = _GraphitiMessages(usage=_FakeUsage())
    wrapped = MeteredAsyncAnthropic(inner=inner)
    graphiti_client = AnthropicClient(
        config=LLMConfig(
            api_key="test-only",
            model="claude-haiku-4-5-20251001",
            small_model="claude-haiku-4-5-20251001",
        ),
        client=wrapped,
    )

    def _metering_down(*args, **kwargs):
        raise RuntimeError("usage database unavailable")

    if settlement_fails:
        monkeypatch.setattr(
            "app.services.metered_async_anthropic_client.record_event",
            _metering_down,
        )
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="graphiti-settlement-gap",
            require_hard_admission=True,
        )
    )
    try:
        result = _run(
            graphiti_client.generate_response(
                [
                    Message(role="system", content="Return JSON."),
                    Message(role="user", content="Confirm acceptance."),
                ],
                max_tokens=100,
            )
        )
    finally:
        graph_metering_ctx.reset(token)

    assert result == {"accepted": True}
    assert len(inner.messages.create_calls) == 1
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == expected_state
    if usage is None:
        assert hold.entry_metadata["deferred_usage_event"] is None
    else:
        assert hold.entry_metadata["deferred_usage_event"]["input_tokens"] == 1_000
    assert db.query(UsageEvent).count() == 0
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == expected_status)
        .count()
        == 1
    )
