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
from app.services.claude_model_pricing import UnpriceableClaudeModelError
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
)
from app.services.pricing_service import Feature
from app.services import provider_retry_policy
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
    stop_reason: str = "end_turn"


class _FakeStream:
    """The object yielded by ``with client.messages.stream(...) as stream``.
    Only needs ``get_final_message`` for the wrapper's metering hook."""

    def __init__(self, *, usage: _FakeUsage):
        self._usage = usage
        self.current_message_snapshot = _FakeFinalMessage(usage=usage)

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
        self.stream_calls = 0

    def stream(self, **_: Any) -> _FakeStreamCM:
        self.stream_calls += 1
        return _FakeStreamCM(usage=self._usage)


class _FakeAnthropic:
    def __init__(self, *, usage: _FakeUsage):
        self.messages = _FakeMessages(usage=usage)


def test_unpriceable_stream_is_blocked_before_reservation_or_sdk(db):
    org = Organization(
        name="Unpriceable stream",
        slug=f"unpriceable-stream-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.commit()
    inner = _FakeAnthropic(usage=_FakeUsage())
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))
    unknown = "claude-opus-99-untrusted-secret-marker"

    with pytest.raises(UnpriceableClaudeModelError) as error:
        client.messages.stream(
            model=unknown,
            messages=[],
            metering={"feature": Feature.TAALI_CHAT},
        )

    assert unknown not in str(error.value)
    assert inner.messages.stream_calls == 0
    assert db.query(BillingCreditLedger).count() == 0
    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0


def test_stream_exit_writes_call_log_row(db):
    """Driving a streaming call through MeteredAnthropicClient writes a
    claude_call_log row on the way out — with real tokens, FK-linked to
    the usage_event we already wrote."""
    org = Organization(name="O", slug=f"o-{id(db)}-stream")
    db.add(org); db.commit()

    inner = _FakeAnthropic(
        usage=_FakeUsage(input_tokens=512, output_tokens=128)
    )
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[],
        metering={"feature": Feature.TAALI_CHAT},
    ) as _stream:
        # Caller drains the stream — we don't iterate in this test, the
        # wrapper's __exit__ is what matters.
        pass

    from app.platform.database import SessionLocal
    with SessionLocal() as s:
        rows = s.query(ClaudeCallLog).filter(
            ClaudeCallLog.organization_id == int(org.id),
            ClaudeCallLog.model == "claude-haiku-4-5-20251001",
        ).all()
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


def test_stream_exit_error_re_raises_after_persisting_captured_usage(db):
    org = Organization(name="Exit error", slug=f"exit-error-{id(db)}")
    db.add(org)
    db.commit()
    usage = _FakeUsage(input_tokens=321, output_tokens=45)

    class _ExitErrorMessages(_FakeMessages):
        def stream(self, **_: Any) -> _FakeStreamCM:
            outer = _FakeStreamCM(usage=self._usage)

            def _raise_after_usage(_self, _exc_type, _exc, _tb):
                raise RuntimeError("stream transport close failed")

            outer.__class__ = type(
                "_ExitErrorCM",
                (_FakeStreamCM,),
                {"__exit__": _raise_after_usage},
            )
            return outer

    inner = type("Inner", (), {"messages": _ExitErrorMessages(usage=usage)})()
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with pytest.raises(RuntimeError, match="transport close failed"):
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[],
            metering={"feature": Feature.TAALI_CHAT},
        ):
            pass

    event = db.query(UsageEvent).filter_by(organization_id=int(org.id)).one()
    log = db.query(ClaudeCallLog).filter_by(organization_id=int(org.id)).one()
    assert event.input_tokens == 321
    assert event.output_tokens == 45
    assert log.usage_event_id == int(event.id)
    assert log.status == "interrupted"


def test_cancelled_stream_closes_without_draining_remaining_provider_output(db):
    org = Organization(name="Cancelled stream", slug=f"cancelled-stream-{id(db)}")
    db.add(org)
    db.commit()

    class _TrackingStream:
        def __init__(self):
            self.final_message_calls = 0

        def get_final_message(self):
            self.final_message_calls += 1
            return _FakeFinalMessage(
                usage=_FakeUsage(input_tokens=99, output_tokens=9)
            )

    class _TrackingCM:
        def __init__(self):
            self.stream = _TrackingStream()
            self.exit_calls = 0

        def __enter__(self):
            return self.stream

        def __exit__(self, exc_type, exc, tb):
            self.exit_calls += 1
            return False

    inner_cm = _TrackingCM()
    client = MeteredAnthropicClient(
        inner=type(
            "_Client",
            (),
            {"messages": type("_Messages", (), {"stream": lambda self, **_: inner_cm})()},
        )(),
        organization_id=int(org.id),
    )

    with pytest.raises(GeneratorExit):
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[],
            metering={"feature": Feature.TAALI_CHAT},
        ):
            raise GeneratorExit

    assert inner_cm.exit_calls == 1
    assert inner_cm.stream.final_message_calls == 0
    log = db.query(ClaudeCallLog).filter_by(organization_id=int(org.id)).one()
    assert log.status == "interrupted"
    assert log.usage_event_id is None


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
        inner=_FakeAnthropic(
            usage=_FakeUsage(input_tokens=512, output_tokens=128)
        ),
        organization_id=int(org.id),
    )
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
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
        max_tokens=100,
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "role_id": int(role.id),
            "trace_id": "stream:unused",
        },
    )

    assert db.query(BillingCreditLedger).count() == 0


def test_entered_stream_without_usage_retains_unknown_provider_hold(
    db, monkeypatch,
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
        max_tokens=100,
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
    assert (
        hold.entry_metadata["state"]
        == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    )
    assert db.query(UsageEvent).count() == 0
    log = db.query(ClaudeCallLog).filter_by(organization_id=int(org.id)).one()
    assert log.status == "no_usage_on_response"
    assert log.usage_event_id is None
    assert db.get(Organization, int(org.id)).credits_balance < 1_000_000


@pytest.mark.parametrize("failure_mode", ["body", "final_snapshot", "inner_exit"])
def test_no_usage_stream_failures_retain_hold_and_persist_interrupted_evidence(
    db,
    monkeypatch,
    failure_mode,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    org = Organization(
        name=f"No usage {failure_mode}",
        slug=f"no-usage-{failure_mode}-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name=f"Role {failure_mode}")
    db.add(role)
    db.commit()

    class _NoUsageStream:
        @property
        def current_message_snapshot(self):
            if failure_mode == "final_snapshot":
                raise RuntimeError("final snapshot unavailable")
            return _FakeFinalMessage(usage=None)

    class _NoUsageCM:
        def __enter__(self):
            return _NoUsageStream()

        def __exit__(self, exc_type, exc, tb):
            if failure_mode == "inner_exit":
                raise RuntimeError("inner stream close failed")
            return False

    class _NoUsageMessages:
        def stream(self, **_kwargs):
            return _NoUsageCM()

    client = MeteredAnthropicClient(
        inner=type("_Client", (), {"messages": _NoUsageMessages()})(),
        organization_id=int(org.id),
    )

    def _consume():
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[],
            metering={
                "feature": Feature.TAALI_CHAT,
                "role_id": int(role.id),
                "trace_id": f"stream:{failure_mode}",
            },
        ):
            if failure_mode == "body":
                raise ValueError("consumer failed")

    if failure_mode == "body":
        with pytest.raises(ValueError, match="consumer failed"):
            _consume()
    elif failure_mode == "inner_exit":
        with pytest.raises(RuntimeError, match="close failed"):
            _consume()
    else:
        _consume()

    db.expire_all()
    hold = db.query(BillingCreditLedger).filter_by(
        reason="reservation:taali_chat"
    ).one()
    log = db.query(ClaudeCallLog).filter_by(organization_id=int(org.id)).one()
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    assert log.status == "interrupted"
    assert log.usage_event_id is None
    assert db.query(UsageEvent).count() == 0


def test_stream_enter_timeout_retains_ambiguous_attempt_hold(
    db, monkeypatch, caplog
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        provider_retry_policy,
        "sleep_before_retry",
        lambda **_kwargs: None,
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
    secret_marker = "stream-provider-secret-must-not-be-logged"

    class _FailEnterCM:
        def __enter__(self):
            raise TimeoutError(secret_marker)

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
            max_tokens=100,
            messages=[],
            metering={
                "feature": Feature.TAALI_CHAT,
                "role_id": int(role.id),
                "trace_id": "stream:ambiguous-enter",
            },
        ):
            pass

    db.expire_all()
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:taali_chat")
        .order_by(BillingCreditLedger.id.asc())
        .all()
    )
    assert len(holds) == 2
    assert holds[0].external_ref != holds[1].external_ref
    assert all(
        hold.entry_metadata["state"] == "provider_attempt_started"
        for hold in holds
    )
    logs = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .order_by(ClaudeCallLog.id.asc())
        .all()
    )
    assert [row.retry_attempt for row in logs] == [0, 1]
    assert logs[1].parent_call_log_id == logs[0].id
    assert secret_marker not in caplog.text


def test_stream_retry_stops_when_failure_log_cannot_persist(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        provider_retry_policy,
        "sleep_before_retry",
        lambda **_kwargs: None,
    )
    org = Organization(
        name="Stream evidence failure",
        slug=f"stream-evidence-fail-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Stream evidence role")
    db.add(role)
    db.commit()

    class _FailEnterCM:
        def __enter__(self):
            raise TimeoutError("provider result uncertain")

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FailEnterMessages:
        def __init__(self):
            self.calls = 0

        def stream(self, **kwargs):
            self.calls += 1
            return _FailEnterCM()

    messages = _FailEnterMessages()
    client = MeteredAnthropicClient(
        inner=type("_Client", (), {"messages": messages})(),
        organization_id=int(org.id),
    )
    monkeypatch.setattr(client.messages, "_record_call_log_safe", lambda **_: None)

    with pytest.raises(
        provider_retry_policy.ProviderRetryEvidenceUnavailableError,
        match="retry evidence",
    ):
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[],
            metering={
                "feature": Feature.TAALI_CHAT,
                "role_id": int(role.id),
                "trace_id": "stream:evidence-down",
            },
        ):
            pass

    assert messages.calls == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:taali_chat")
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"


def test_stream_enter_timeout_then_success_attributes_both_attempts(
    db, monkeypatch
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        provider_retry_policy,
        "sleep_before_retry",
        lambda **_kwargs: None,
    )
    org = Organization(
        name="Stream retry success",
        slug=f"stream-retry-success-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Stream retry role")
    db.add(role)
    db.commit()

    class _TimeoutCM:
        def __enter__(self):
            raise TimeoutError("first stream response timed out")

        def __exit__(self, exc_type, exc, tb):
            return False

    class _TimeoutThenSuccessMessages(_FakeMessages):
        def stream(self, **_: Any):
            self.stream_calls += 1
            if self.stream_calls == 1:
                return _TimeoutCM()
            return _FakeStreamCM(usage=self._usage)

    usage = _FakeUsage(input_tokens=25, output_tokens=5)
    messages = _TimeoutThenSuccessMessages(usage=usage)
    client = MeteredAnthropicClient(
        inner=type("_Client", (), {"messages": messages})(),
        organization_id=int(org.id),
    )

    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[],
        metering={
            "feature": Feature.TAALI_CHAT,
            "role_id": int(role.id),
            "trace_id": "stream:timeout-then-success",
        },
    ):
        pass

    assert messages.stream_calls == 2
    db.expire_all()
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:taali_chat")
        .order_by(BillingCreditLedger.id.asc())
        .all()
    )
    assert len(holds) == 2
    assert holds[0].external_ref != holds[1].external_ref
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
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 1
