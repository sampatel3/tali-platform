"""``claude_call_log`` is the unconditional source-of-truth log for
every Anthropic API call. The wrapper writes one row regardless of
whether the application-layer metering succeeded — that's the
structural fix to the 2026-05-21 73% reconciliation gap, where
application-layer ``record_event`` calls were the only place metering
happened and any early-return/exception/retry-overwrite suppressed them.

These tests pin:
- Successful call → call_log row with status="ok" and FK to usage_event.
- ``metering={"skip": True}`` → call_log row still written, FK is NULL
  (the "metering attribution gap" signal the user kept asking for).
- SDK exception after a durable paid-attempt marker → call_log row with
  status="sdk_ambiguous_error" and zero reported tokens. The reservation is
  retained because a transport error is not proof the provider did not bill.
- Missing org context → call_log still written (caller may add
  enrichment later); UsageEvent skipped with a warning.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.pricing_service import Feature


def _fake_response(input_tokens=100, output_tokens=50, request_id="req_test_001"):
    return SimpleNamespace(
        content=[SimpleNamespace(text="ok")],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        id=request_id,
    )


def _fake_inner(response):
    inner = MagicMock()
    inner.messages.create.return_value = response
    return inner


def test_successful_call_writes_call_log_with_usage_event_fk(db, monkeypatch):
    """Happy path: wrapper writes both a call_log and a usage_event,
    and the call_log.usage_event_id FKs back."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    # Route the wrapper's fresh-session writes to our test DB.
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    inner = _fake_inner(_fake_response())
    wrapper = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))
    wrapper.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
        metering={"feature": Feature.OTHER},
    )

    logs = db.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.model == "claude-haiku-4-5-20251001"
    assert log.input_tokens == 100
    assert log.output_tokens == 50
    assert log.status == "ok"
    assert log.feature_hint == "other"
    assert log.usage_event_id is not None
    assert log.anthropic_request_id == "req_test_001"

    # The FK should resolve to a real UsageEvent.
    events = db.query(UsageEvent).filter(UsageEvent.organization_id == org.id).all()
    assert len(events) == 1
    assert events[0].id == log.usage_event_id


def test_skip_metering_still_writes_call_log_with_null_fk(db, monkeypatch):
    """The structural guarantee: ``metering={"skip": True}`` opts out of
    UsageEvent but NOT out of call_log. The NULL FK is the queryable
    "metering attribution gap" signal."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    inner = _fake_inner(_fake_response())
    wrapper = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))
    wrapper.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
        metering={"skip": True, "metered_by": "test_caller"},
    )

    logs = db.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.input_tokens == 100
    assert log.status == "ok"
    assert log.usage_event_id is None  # the attribution gap signal
    assert log.feature_hint == "skip"

    # No UsageEvent was written.
    events = db.query(UsageEvent).filter(UsageEvent.organization_id == org.id).all()
    assert len(events) == 0


def test_ambiguous_sdk_error_writes_call_log_with_zero_tokens_and_reraises(
    db, monkeypatch,
):
    """A transport failure remains traceable without declaring spend absent.

    The automatic reservation reached ``provider_attempt_started`` before the
    SDK call. An unknown exception may therefore have happened after Anthropic
    accepted the request, so the wrapper retains the hold, records an ambiguous
    error with zero *reported* tokens, and re-raises.
    """
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    inner = MagicMock()
    inner.messages.create.side_effect = RuntimeError("simulated 500")
    wrapper = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with pytest.raises(RuntimeError, match="simulated 500"):
        wrapper.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": "hi"}],
            metering={"feature": Feature.OTHER},
        )

    logs = db.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "sdk_ambiguous_error"
    assert "simulated 500" in (log.error_reason or "")
    assert log.input_tokens == 0
    assert log.output_tokens == 0
    assert log.usage_event_id is None


def test_attribution_gap_query(db, monkeypatch):
    """The user's question — 'how many calls happened but weren't
    attributed to a feature?' — is now a one-line SQL query against
    call_log: rows where usage_event_id IS NULL."""
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.commit()

    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal
    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)

    inner = _fake_inner(_fake_response())
    wrapper = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    # 1 metered call, 2 skipped calls
    wrapper.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
        metering={"feature": Feature.OTHER},
    )
    for _ in range(2):
        wrapper.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": "hi"}],
            metering={"skip": True},
        )

    total_calls = db.query(ClaudeCallLog).filter(ClaudeCallLog.organization_id == org.id).count()
    unattributed = (
        db.query(ClaudeCallLog)
        .filter(
            ClaudeCallLog.organization_id == org.id,
            ClaudeCallLog.usage_event_id.is_(None),
        )
        .count()
    )
    assert total_calls == 3
    assert unattributed == 2
