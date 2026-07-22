"""Stream path must write a claude_call_log row.

Pre-#387 the wrapper's ``_MeteredStreamCtx.__exit__`` wrote a
usage_event but silently skipped the claude_call_log row — breaking
the #237 "every call writes a call_log row" invariant for the stream
path. Only ``taali_chat`` streams in prod today (small volume) but
the gap was real and any future streaming caller would have widened it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.components.ai_routing.model_registry import (
    ANTHROPIC_HAIKU_4_5,
    DEFAULT_MODEL_REGISTRY,
)
from app.components.ai_routing.pricing import RoutedPricingReceiptError
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    _MeteredMessages,
)
from app.services.pricing_service import Feature
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
)


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _FakeFinalMessage:
    usage: _FakeUsage
    id: str = "msg-stream-final"
    model: str = "claude-haiku-4-5-20251001"


class _FakeStream:
    """The object yielded by ``with client.messages.stream(...) as stream``.
    Only needs ``get_final_message`` for the wrapper's metering hook."""

    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage

    def get_final_message(self) -> _FakeFinalMessage:
        return _FakeFinalMessage(usage=self._usage)


class _FakeStreamCM:
    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage

    def __enter__(self):
        return _FakeStream(usage=self._usage)

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeMessages:
    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage

    def stream(self, **_: Any) -> _FakeStreamCM:
        return _FakeStreamCM(usage=self._usage)


class _FakeAnthropic:
    def __init__(self, *, usage: _FakeUsage):
        self.messages = _FakeMessages(usage=usage)


def test_stream_exit_writes_call_log_row(db):
    """Driving a streaming call through MeteredAnthropicClient writes a
    claude_call_log row on the way out — with real tokens, FK-linked to
    the usage_event we already wrote."""
    org = Organization(name="O", slug=f"o-{id(db)}-stream")
    db.add(org)
    db.commit()

    inner = _FakeAnthropic(usage=_FakeUsage(input_tokens=512, output_tokens=128))
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={"feature": Feature.TAALI_CHAT},
    ) as _stream:
        # Caller drains the stream — we don't iterate in this test, the
        # wrapper's __exit__ is what matters.
        pass

    from app.platform.database import SessionLocal

    with SessionLocal() as s:
        rows = (
            s.query(ClaudeCallLog)
            .filter(
                ClaudeCallLog.organization_id == int(org.id),
                ClaudeCallLog.model == "claude-haiku-4-5-20251001",
            )
            .all()
        )
        # ONE row per stream call.
        assert len(rows) == 1, f"expected 1 call_log row, got {len(rows)}"
        row = rows[0]
        assert row.input_tokens == 512
        assert row.output_tokens == 128
        assert row.feature_hint == "taali_chat"
        # FK-linked to the usage_event written by the same exit hook.
        assert row.usage_event_id is not None
        # Clean up so other tests have an empty table.
        s.query(ClaudeCallLog).delete()
        s.commit()


def test_hard_reserved_stream_persists_success_receipt(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE",
        True,
    )
    org = Organization(
        name="Stream receipt",
        slug=f"stream-receipt-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Stream role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()

    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=_FakeUsage(input_tokens=512, output_tokens=128)),
        organization_id=int(org.id),
    )
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "role_id": int(role.id),
            "entity_id": "role:stream",
            "trace_id": "stream:receipt",
        },
    ):
        pass

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:taali_chat")
        .one()
    )
    receipt = hold.entry_metadata["deferred_usage_event"]
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    assert receipt["input_tokens"] == 512
    assert receipt["output_tokens"] == 128
    assert receipt["role_id"] == int(role.id)
    assert receipt["service_tier"] == "standard"
    assert db.query(UsageEvent).filter_by(role_id=int(role.id)).count() == 1


def test_unused_stream_context_does_not_start_provider_hold(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = Organization(
        name="Unused stream",
        slug=f"unused-stream-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Unused stream role")
    db.add(role)
    db.commit()
    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=_FakeUsage()),
        organization_id=int(org.id),
    )

    client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "role_id": int(role.id),
            "trace_id": "stream:unused",
        },
    )

    assert db.query(BillingCreditLedger).count() == 0


def test_entered_stream_without_usage_retains_unknown_provider_hold(
    db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE",
        True,
    )
    org = Organization(
        name="Unknown stream usage",
        slug=f"unknown-stream-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Unknown stream role")
    db.add(role)
    db.commit()
    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=None),
        organization_id=int(org.id),
    )

    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "role_id": int(role.id),
            "trace_id": "stream:unknown-usage",
        },
    ):
        pass

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:taali_chat")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    assert db.query(UsageEvent).count() == 0
    assert db.get(Organization, int(org.id)).credits_balance < 1_000_000
    call_log = db.query(ClaudeCallLog).one()
    assert call_log.status == "no_usage_on_response"
    assert call_log.anthropic_request_id == "msg-stream-final"


def test_routed_stream_persists_trace_retry_parent_and_request_id(db):
    org = Organization(name="Routed stream", slug=f"routed-stream-{id(db)}")
    db.add(org)
    db.flush()
    parent = ClaudeCallLog(
        organization_id=int(org.id),
        model="claude-haiku-4-5-20251001",
        status="sdk_error",
        trace_id="ai-route:stream-invocation:1",
    )
    db.add(parent)
    db.commit()

    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=_FakeUsage(input_tokens=7, output_tokens=3)),
        organization_id=int(org.id),
    )
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "trace_id": "ai-route:stream-invocation:2",
            "retry_attempt": 1,
            "metadata": {
                "ai_routing": {
                    "invocation_id": "stream-invocation",
                    "attempt_ordinal": 2,
                    "deployment_id": ANTHROPIC_HAIKU_4_5,
                    "registry_version": DEFAULT_MODEL_REGISTRY.version,
                    "region": "global",
                }
            },
        },
    ):
        pass

    db.expire_all()
    row = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.trace_id == "ai-route:stream-invocation:2")
        .one()
    )
    assert row.status == "ok"
    assert row.retry_attempt == 1
    assert row.parent_call_log_id == int(parent.id)
    assert row.anthropic_request_id == "msg-stream-final"


def test_interrupted_stream_without_usage_writes_ambiguous_evidence(db):
    org = Organization(name="Interrupted stream", slug=f"stream-cut-{id(db)}")
    db.add(org)
    db.commit()
    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=None),
        organization_id=int(org.id),
    )

    with pytest.raises(RuntimeError, match="client disconnected"):
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            messages=[],
            metering={
                "feature": Feature.TAALI_CHAT,
                "trace_id": "stream:interrupted-no-usage",
            },
        ):
            raise RuntimeError("client disconnected")

    db.expire_all()
    row = db.query(ClaudeCallLog).one()
    assert row.status == "interrupted_no_usage"
    assert row.trace_id == "stream:interrupted-no-usage"
    assert row.anthropic_request_id == "msg-stream-final"


def test_routed_stream_receipt_error_is_traced_before_propagation(db):
    org = Organization(name="Bad stream receipt", slug=f"bad-stream-{id(db)}")
    db.add(org)
    db.commit()
    usage = _FakeUsage(input_tokens=5, output_tokens=2)
    usage.input_tokens = "invalid"  # type: ignore[assignment]
    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=usage),
        organization_id=int(org.id),
    )

    with pytest.raises(RoutedPricingReceiptError, match="exact integer"):
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            messages=[],
            metering={
                "feature": Feature.TAALI_CHAT,
                "trace_id": "ai-route:bad-stream:1",
                "metadata": {
                    "ai_routing": {
                        "invocation_id": "bad-stream",
                        "attempt_ordinal": 1,
                        "deployment_id": ANTHROPIC_HAIKU_4_5,
                        "registry_version": DEFAULT_MODEL_REGISTRY.version,
                        "region": "global",
                    }
                },
            },
        ):
            pass

    db.expire_all()
    row = db.query(ClaudeCallLog).one()
    assert row.status == "routed_pricing_receipt_error"
    assert row.trace_id == "ai-route:bad-stream:1"
    assert row.anthropic_request_id == "msg-stream-final"
    assert row.cost_usd_micro == 0


@pytest.mark.parametrize(
    ("interrupted", "expected_status"),
    [
        (False, "metering_error_completed"),
        (True, "metering_error_interrupted"),
    ],
)
def test_stream_metering_failure_status_preserves_completion_state(
    db, monkeypatch, interrupted, expected_status
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(_MeteredMessages, "_record_from_usage", lambda *_a, **_k: None)
    org = Organization(
        name=f"Metering status {interrupted}",
        slug=f"stream-meter-status-{interrupted}-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Metering failure role")
    db.add(role)
    db.commit()
    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(usage=_FakeUsage(input_tokens=5, output_tokens=2)),
        organization_id=int(org.id),
    )

    context = client.messages.stream(
        model="claude-haiku-4-5-20251001",
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "role_id": int(role.id),
            "trace_id": f"stream:metering:{interrupted}",
        },
    )
    if interrupted:
        with pytest.raises(RuntimeError, match="cut"):
            with context:
                raise RuntimeError("cut")
    else:
        with context:
            pass

    db.expire_all()
    row = db.query(ClaudeCallLog).one()
    assert row.status == expected_status


def test_stream_enter_timeout_retains_ambiguous_attempt_hold(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = Organization(
        name="Ambiguous stream enter",
        slug=f"ambiguous-stream-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Ambiguous stream role")
    db.add(role)
    db.commit()

    class _FailEnterCM:
        def __enter__(self):
            raise TimeoutError("stream response timed out")

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FailEnterMessages:
        def stream(self, **kwargs):
            return _FailEnterCM()

    client = MeteredAnthropicClient(
        inner=type("_Client", (), {"messages": _FailEnterMessages()})(),
        organization_id=int(org.id),
    )

    with pytest.raises(TimeoutError):
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            messages=[],
            metering={
                "feature": Feature.TAALI_CHAT,
                "role_id": int(role.id),
                "trace_id": "stream:ambiguous-enter",
            },
        ):
            pass

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:taali_chat")
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .count()
        == 1
    )
