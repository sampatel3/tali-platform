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
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from graphiti_core.embedder.voyage import VoyageAIEmbedder

from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services import metered_voyage_embedder as voyage_module
from app.services import voyage_call_log as voyage_call_log_module
from app.services.metered_async_anthropic_client import (
    GraphMeteringContext,
    GraphProviderAdmissionError,
    graph_metering_ctx,
)
from app.services.metered_voyage_embedder import (
    MeteredVoyageClient,
    UnsupportedVoyageSurfaceError,
    wrap_voyage_embedder,
)
from app.services.pricing_service import (
    Feature,
    credits_charged,
    is_voyage_model,
    voyage_cost_micro,
)
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
)
from app.services import provider_retry_policy
from app.services.voyage_pricing import UnpriceableVoyageModelError
from app.services.usage_metering_service import record_event
from app.services.usage_metering_service import InsufficientCreditsError


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
    return asyncio.run(coro)


def _enable_live_holds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------


def test_voyage_cost_micro_prices_voyage_3():
    assert is_voyage_model("voyage-3") is True
    assert is_voyage_model("claude-haiku-4-5") is False
    # $0.06 / 1M tokens == 0.06 micro-USD / token.
    assert voyage_cost_micro(model="voyage-3", input_tokens=1_000_000) == 60_000
    assert voyage_cost_micro(model="voyage-3", input_tokens=100_000) == 6_000
    with pytest.raises(UnpriceableVoyageModelError):
        voyage_cost_micro(model="voyage-9-future", input_tokens=1_000_000)
    assert voyage_cost_micro(model="voyage-4-large", input_tokens=1_000_000) == 120_000


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


def test_record_event_rejects_unknown_voyage_model_instead_of_guessing(db):
    org = Organization(name="O", slug=f"o-{id(db)}-voy-unknown")
    db.add(org)
    db.commit()

    with pytest.raises(UnpriceableVoyageModelError):
        record_event(
            db,
            organization_id=int(org.id),
            feature=Feature.GRAPH_SYNC,
            model="voyage-9-future",
            input_tokens=500_000,
            output_tokens=0,
        )

    assert db.query(UsageEvent).count() == 0


# --------------------------------------------------------------------------
# Wire-tap
# --------------------------------------------------------------------------


def test_graph_outbox_marker_runs_immediately_before_voyage_sdk(db):
    order: list[str] = []
    org = Organization(name="Voyage marker org", slug=f"voy-marker-{id(db)}")
    db.add(org)
    db.commit()

    class _OrderedVoyage(_FakeVoyageClient):
        async def embed(self, texts, model=None, **kwargs):
            order.append("sdk")
            return await super().embed(texts, model=model, **kwargs)

    inner = _OrderedVoyage(total_tokens=3)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            provider_attempt_callback=lambda: order.append("marker") or True,
        )
    )
    try:
        _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert order == ["marker", "sdk"]


def test_omitted_voyage_model_is_injected_as_the_metered_default(db):
    org = Organization(name="Voyage default org", slug=f"voy-default-{id(db)}")
    db.add(org)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=3)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(organization_id=int(org.id))
    )
    try:
        _run(wrapped.embed(["hello"]))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == [{"texts": ["hello"], "model": "voyage-3"}]
    event = db.query(UsageEvent).one()
    assert event.model == "voyage-3"


def test_failed_graph_outbox_marker_blocks_voyage_sdk(db):
    org = Organization(
        name="Voyage blocked marker org", slug=f"voy-blocked-marker-{id(db)}"
    )
    db.add(org)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=3)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            provider_attempt_callback=lambda: False,
        )
    )
    try:
        with pytest.raises(GraphProviderAdmissionError, match="graph-ingest attempt"):
            _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == []


def test_embed_without_ctx_fails_before_voyage_or_metering(db):
    inner = _FakeVoyageClient(total_tokens=12_000)
    wrapped = MeteredVoyageClient(inner)

    with pytest.raises(GraphProviderAdmissionError, match="organization"):
        _run(wrapped.embed(["hello", "world"], model="voyage-3"))

    assert inner.calls == []
    assert db.query(ClaudeCallLog).count() == 0
    assert db.query(UsageEvent).count() == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("organization_id", True, "organization"),
        ("organization_id", "1", "organization"),
        ("organization_id", 1.0, "organization"),
        ("organization_id", 0, "organization"),
        ("role_id", True, "role"),
        ("role_id", "1", "role"),
        ("role_id", 1.0, "role"),
        ("user_id", True, "user"),
        ("user_id", "1", "user"),
        ("user_id", 1.0, "user"),
        ("candidate_id", True, "candidate"),
        ("candidate_id", "1", "candidate"),
        ("candidate_id", 1.0, "candidate"),
    ],
)
def test_embed_rejects_coercible_attribution_before_voyage(
    db,
    field,
    value,
    message,
):
    org = Organization(name="Voyage attribution", slug=f"voy-attr-{id(db)}-{field}")
    db.add(org)
    db.commit()
    context: dict[str, Any] = {"organization_id": int(org.id)}
    context[field] = value
    inner = _FakeVoyageClient(total_tokens=1)
    token = graph_metering_ctx.set(GraphMeteringContext(**context))
    try:
        with pytest.raises(GraphProviderAdmissionError, match=message):
            _run(MeteredVoyageClient(inner).embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == []
    assert db.query(BillingCreditLedger).count() == 0


def test_embed_with_ctx_links_usage_event(db):
    org = Organization(name="O", slug=f"o-{id(db)}-voy-ctx")
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Voyage context role")
    db.add(role)
    db.commit()

    wrapped = MeteredVoyageClient(_FakeVoyageClient(total_tokens=200_000))
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            candidate_id=7,
            role_id=int(role.id),
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


def test_flag_false_still_hard_reserves_and_blocks_zero_credit(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage universal hold",
        slug=f"voy-universal-{id(db)}",
        credits_balance=0,
    )
    db.add(org)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=1)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            require_hard_admission=False,
        )
    )
    try:
        with pytest.raises(InsufficientCreditsError):
            _run(MeteredVoyageClient(inner).embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == []
    assert db.query(BillingCreditLedger).count() == 0


def test_unknown_model_and_unreviewed_surfaces_never_touch_inner(db):
    org = Organization(name="Voyage guard", slug=f"voy-guard-{id(db)}")
    db.add(org)
    db.commit()

    class _GuardedInner(_FakeVoyageClient):
        @property
        def rerank(self):
            raise AssertionError("inner rerank surface was accessed")

    inner = _GuardedInner(total_tokens=1)
    wrapped = MeteredVoyageClient(inner)
    with pytest.raises(UnsupportedVoyageSurfaceError):
        _ = wrapped.rerank
    token = graph_metering_ctx.set(GraphMeteringContext(organization_id=int(org.id)))
    try:
        with pytest.raises(ValueError, match="exact reviewed pricing"):
            _run(wrapped.embed(["hello"], model="voyage-unknown-future"))
    finally:
        graph_metering_ctx.reset(token)
    assert inner.calls == []


@pytest.mark.parametrize("model", [False, 0, ""])
def test_explicit_invalid_model_is_not_treated_as_omitted_default(db, model):
    org = Organization(name="Voyage exact model", slug=f"voy-exact-{id(db)}")
    db.add(org)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=1)
    token = graph_metering_ctx.set(
        GraphMeteringContext(organization_id=int(org.id))
    )
    try:
        with pytest.raises(UnpriceableVoyageModelError):
            _run(MeteredVoyageClient(inner).embed(["hello"], model=model))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == []


def test_wrap_voyage_embedder_fails_closed_if_client_cannot_be_replaced():
    class _UnwrappableEmbedder:
        @property
        def client(self):
            return _FakeVoyageClient(total_tokens=1)

        @client.setter
        def client(self, _value):
            raise RuntimeError("client is immutable")

    with pytest.raises(RuntimeError, match="immutable"):
        wrap_voyage_embedder(_UnwrappableEmbedder())


def test_voyage_client_rejects_hidden_sdk_retries():
    inner = _FakeVoyageClient(total_tokens=1)
    inner.max_retries = 1

    with pytest.raises(UnsupportedVoyageSurfaceError, match="retries"):
        MeteredVoyageClient(inner)


def test_hard_admission_voyage_reserves_then_settles_actual_usage(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage Hold Org",
        slug=f"voy-hold-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Voyage Hold Role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()

    inner = _FakeVoyageClient(total_tokens=10_000)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            candidate_id=88,
            episode_name="voyage-settlement",
            trace_id="graph-outbox:voyage-settlement",
            require_hard_admission=True,
        )
    )
    try:
        result = _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert result.embeddings
    assert len(inner.calls) == 1
    db.expire_all()
    event = db.query(UsageEvent).one()
    refreshed_org = db.query(Organization).filter(Organization.id == org.id).one()
    assert event.role_id == int(role.id)
    assert event.entity_id == "88"
    assert event.event_metadata["provider"] == "voyage"
    assert event.event_metadata["trace_id"] == "graph-outbox:voyage-settlement"
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert refreshed_org.credits_balance == 100_000 - int(event.credits_charged)
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref.like("%:settled"))
        .count()
        == 1
    )
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    assert hold.entry_metadata["deferred_usage_event"]["input_tokens"] == 10_000


def test_voyage_missing_usage_retains_unknown_hold_and_returns_embedding(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage missing usage",
        slug=f"voy-missing-usage-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Voyage missing usage role")
    db.add(role)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=0)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="voyage-missing-usage",
            require_hard_admission=True,
        )
    )
    try:
        result = _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert result.embeddings == [[0.1, 0.2, 0.3]]
    assert len(inner.calls) == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    evidence = db.query(ClaudeCallLog).one()
    assert evidence.status == "no_usage_on_response"


def test_pinned_graphiti_voyage_does_not_replay_success_after_settlement_gap(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Pinned Graphiti Voyage",
        slug=f"pinned-graphiti-voyage-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Pinned Graphiti Voyage role")
    db.add(role)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=12)
    wrapped = MeteredVoyageClient(inner)
    embedder = object.__new__(VoyageAIEmbedder)
    embedder.client = wrapped
    embedder.config = SimpleNamespace(embedding_model="voyage-3", embedding_dim=3)

    def _metering_down(*_args, **_kwargs):
        raise RuntimeError("usage database unavailable")

    monkeypatch.setattr(voyage_module, "record_event", _metering_down)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="pinned-graphiti-voyage-settlement-gap",
            require_hard_admission=True,
        )
    )
    try:
        result = _run(embedder.create("hello"))
    finally:
        graph_metering_ctx.reset(token)

    assert result == [0.1, 0.2, 0.3]
    assert len(inner.calls) == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    assert hold.entry_metadata["deferred_usage_event"]["input_tokens"] == 12
    evidence = db.query(ClaudeCallLog).one()
    assert evidence.status == "metering_error"


def test_hard_admission_zero_credits_never_calls_voyage(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage Empty Org",
        slug=f"voy-empty-{id(db)}",
        credits_balance=0,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Voyage Empty Role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()

    inner = _FakeVoyageClient(total_tokens=10_000)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="voyage-zero-credit",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(InsufficientCreditsError):
            _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == []
    assert db.query(UsageEvent).count() == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_workspace_search_hard_admits_org_without_inventing_role(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage Search Org",
        slug=f"voy-search-{id(db)}",
        credits_balance=100_000,
        # Workspace Pause only denies autonomous role-owned work. This query
        # represents an explicit workspace-level operation and must continue.
        agent_workspace_paused_at=datetime.now(timezone.utc),
    )
    db.add(org)
    db.commit()
    inner = _FakeVoyageClient(total_tokens=5_000)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            episode_name="graph_search:predicate",
            trace_id="graph-search:predicate",
            require_hard_admission=True,
        )
    )
    try:
        _run(wrapped.embed(["query"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    event = db.query(UsageEvent).one()
    assert event.organization_id == int(org.id)
    assert event.role_id is None
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref.like("%:settled"))
        .count()
        == 1
    )


def test_role_pause_after_first_call_blocks_next_autonomous_voyage(
    db, monkeypatch,
):
    """Graphiti's later embedding calls see a role pause made mid-episode."""

    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage Pause Org",
        slug=f"voy-pause-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Voyage Pause Role",
        monthly_usd_budget_cents=100,
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.commit()

    inner = _FakeVoyageClient(total_tokens=100)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            episode_name="pause-between-voyage-calls",
            require_hard_admission=True,
            require_role_admission=True,
        )
    )
    try:
        _run(wrapped.embed(["first"], model="voyage-3"))
        role.agent_paused_at = datetime.now(timezone.utc)
        db.commit()

        with pytest.raises(
            GraphProviderAdmissionError,
            match="role agent is paused",
        ):
            _run(wrapped.embed(["second"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert len(inner.calls) == 1


def test_ambiguous_voyage_failure_retains_attempt_hold(db, monkeypatch, caplog):
    _enable_live_holds(monkeypatch)
    async def _no_wait(**_kwargs):
        return None

    monkeypatch.setattr(
        provider_retry_policy,
        "async_sleep_before_retry",
        _no_wait,
    )
    org = Organization(
        name="Voyage ambiguous",
        slug=f"voy-ambiguous-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Voyage ambiguous role")
    db.add(role)
    db.commit()

    secret_marker = "voyage-provider-response-secret-must-not-escape"

    class _TimeoutVoyage:
        def __init__(self):
            self.calls = 0

        async def embed(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError(secret_marker)

    inner = _TimeoutVoyage()
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="voyage:ambiguous",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(TimeoutError):
            _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .order_by(BillingCreditLedger.id.asc())
        .all()
    )
    assert inner.calls == 2
    assert len(holds) == 2
    assert holds[0].external_ref != holds[1].external_ref
    assert all(
        hold.entry_metadata["state"] == "provider_attempt_started"
        for hold in holds
    )
    exact_request_bound = credits_charged(
        feature=Feature.GRAPH_SYNC,
        cost_usd_micro=voyage_cost_micro(
            model="voyage-3",
            input_tokens=len("hello".encode("utf-8")),
        ),
    )
    assert all(-int(hold.delta) == exact_request_bound for hold in holds)
    assert exact_request_bound < 100
    assert (
        db.get(Organization, int(org.id)).credits_balance
        == 100_000 - (2 * exact_request_bound)
    )
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .count()
        == 2
    )
    evidence = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .order_by(ClaudeCallLog.id.asc())
        .all()
    )
    assert [row.retry_attempt for row in evidence] == [0, 1]
    assert evidence[1].parent_call_log_id == evidence[0].id
    assert all(
        row.error_reason == "voyage_embed:TimeoutError" for row in evidence
    )
    assert secret_marker not in caplog.text


def test_voyage_persistence_failures_log_type_only(monkeypatch, caplog):
    secret_marker = "database-password in persistence exception"

    class _FailingSession:
        def __enter__(self):
            raise RuntimeError(secret_marker)

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(voyage_module, "SessionLocal", _FailingSession)
    monkeypatch.setattr(voyage_call_log_module, "SessionLocal", _FailingSession)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=7,
            trace_id="voyage:persistence-privacy",
        )
    )
    try:
        voyage_module._record_voyage_usage(
            model="voyage-3",
            total_tokens=3,
            request_sha256="request-hash",
            strict=False,
        )
        voyage_module._record_voyage_failure_evidence(
            model="voyage-3",
            error=TimeoutError("provider response"),
            status="sdk_ambiguous_error",
            retry_attempt=0,
        )
    finally:
        graph_metering_ctx.reset(token)

    assert "usage_event write failed error_type=RuntimeError" in caplog.text
    assert "claude_call_log write failed" in caplog.text
    assert "provider failure evidence write failed error_type=RuntimeError" in caplog.text
    assert secret_marker not in caplog.text


def test_voyage_timeout_then_success_attributes_both_attempts(db, monkeypatch):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage retry",
        slug=f"voy-retry-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Voyage retry role")
    db.add(role)
    db.commit()

    async def _no_wait(**_kwargs):
        return None

    monkeypatch.setattr(
        provider_retry_policy,
        "async_sleep_before_retry",
        _no_wait,
    )

    class _TimeoutThenSuccess(_FakeVoyageClient):
        async def embed(self, texts, model=None, **kwargs):
            self.calls.append({"texts": texts, "model": model})
            if len(self.calls) == 1:
                raise TimeoutError("first Voyage response timed out")
            return _FakeEmbeddingsObject(self._total_tokens)

    inner = _TimeoutThenSuccess(total_tokens=12)
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="voyage-timeout-retry",
            require_hard_admission=True,
        )
    )
    try:
        result = _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert result.total_tokens == 12
    assert len(inner.calls) == 2
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
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 1


def test_cancelled_voyage_call_retains_hold_and_writes_ambiguous_evidence(
    db, monkeypatch, caplog,
):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage cancellation",
        slug=f"voy-cancel-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Voyage cancellation role")
    db.add(role)
    db.commit()
    secret_marker = "cancelled-voyage-secret-must-not-escape"

    class _CancelledVoyage:
        def __init__(self):
            self.calls = 0

        async def embed(self, *args, **kwargs):
            self.calls += 1
            raise asyncio.CancelledError(secret_marker)

    inner = _CancelledVoyage()
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="voyage-cancelled",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    db.expire_all()
    assert inner.calls == 1
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
    assert evidence.error_reason == "voyage_embed:CancelledError"
    assert secret_marker not in caplog.text


def test_voyage_retry_stops_when_failure_evidence_cannot_persist(
    db, monkeypatch,
):
    _enable_live_holds(monkeypatch)
    org = Organization(
        name="Voyage evidence failure",
        slug=f"voy-evidence-fail-{id(db)}",
        credits_balance=100_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Voyage evidence role")
    db.add(role)
    db.commit()

    async def _no_wait(**_kwargs):
        return None

    monkeypatch.setattr(provider_retry_policy, "async_sleep_before_retry", _no_wait)
    monkeypatch.setattr(
        voyage_module,
        "_record_voyage_failure_evidence",
        lambda **_kwargs: None,
    )

    class _TimeoutVoyage:
        def __init__(self):
            self.calls = 0

        async def embed(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("provider result uncertain")

    inner = _TimeoutVoyage()
    wrapped = MeteredVoyageClient(inner)
    token = graph_metering_ctx.set(
        GraphMeteringContext(
            organization_id=int(org.id),
            role_id=int(role.id),
            trace_id="voyage-evidence-down",
            require_hard_admission=True,
        )
    )
    try:
        with pytest.raises(TimeoutError):
            _run(wrapped.embed(["hello"], model="voyage-3"))
    finally:
        graph_metering_ctx.reset(token)

    assert inner.calls == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:graph_sync")
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
