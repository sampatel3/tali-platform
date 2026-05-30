"""Batch-path spend capture (cost reconciliation pipe 1).

The Message Batches API bypasses the metered Anthropic wrapper, so for a
long time batch results wrote a ``usage_events`` row only — and ONLY when org
context was present. That left two reconciliation holes that produced the
live -20%..-77% Haiku under-count (internal << Anthropic billed):
  1. no ``claude_call_log`` row (the table the Anthropic reconciliation
     prefers), so batch spend was invisible to it; and
  2. when ``organization_id`` was missing the ``usage_event`` was skipped
     entirely, dropping the spend with nothing to reconcile.

``_record_batch_spend`` now ALWAYS writes a call_log row (org nullable),
links the usage_event when org context exists, and prices at the 50% batch
tier. These tests lock that in.
"""

from __future__ import annotations

import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cv_matching import runner_batch
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.platform.database import Base
from app.services.pricing_service import raw_cost_usd_micro

_MODEL = "claude-haiku-4-5-20251001"


@pytest.fixture()
def session_factory(monkeypatch):
    """In-memory SQLite bound to the SessionLocal the helper opens internally."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.platform.database.SessionLocal", Session, raising=False)
    return Session


def _fake_message(*, model=_MODEL, in_tok=1000, out_tok=500):
    usage = types.SimpleNamespace(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cache_creation=None,  # no 1h split exposed
    )
    return types.SimpleNamespace(usage=usage, model=model, id="msg_batch_1")


def _ctx(*, in_tok=1000, out_tok=500):
    # The retrieve loop has already copied usage onto the run context.
    return types.SimpleNamespace(
        trace_id="trace-batch-1",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )


def _seed_org(Session) -> int:
    db = Session()
    try:
        org = Organization(name="Batch Org", slug="batch-org")
        db.add(org)
        db.commit()
        return org.id
    finally:
        db.close()


def test_batch_spend_writes_call_log_and_linked_usage_event(session_factory):
    """With org context: a call_log row AND a linked usage_event, both priced
    at the 50% batch rate, recorded against the resolved dated model."""
    org_id = _seed_org(session_factory)
    submission = types.SimpleNamespace(
        batch_id="batch_x",
        metering_by_custom_id={
            "c1": {"organization_id": org_id, "entity_id": "application:1"}
        },
    )

    runner_batch._record_batch_spend(
        submission, "c1", message=_fake_message(), ctx=_ctx()
    )

    expected_cost = raw_cost_usd_micro(
        input_tokens=1000, output_tokens=500, model=_MODEL, service_tier="batch"
    )
    # Half of the standard price for the same tokens.
    standard_cost = raw_cost_usd_micro(
        input_tokens=1000, output_tokens=500, model=_MODEL
    )
    assert expected_cost == standard_cost // 2 or abs(expected_cost - standard_cost / 2) <= 1

    db = session_factory()
    try:
        logs = db.query(ClaudeCallLog).all()
        events = db.query(UsageEvent).all()
        assert len(logs) == 1, "exactly one call_log row for the batch result"
        assert len(events) == 1, "exactly one usage_event for the batch result"
        log, event = logs[0], events[0]
        # Ground-truth row captured the resolved dated model + batch price.
        assert log.model == _MODEL
        assert log.input_tokens == 1000 and log.output_tokens == 500
        assert log.cost_usd_micro == expected_cost
        assert log.feature_hint == "score" and log.status == "ok"
        assert log.anthropic_request_id == "msg_batch_1"
        # Linked so reconciliation counts the spend exactly once (call_log,
        # not call_log + unlinked usage_event).
        assert log.usage_event_id == event.id
        assert event.cost_usd_micro == expected_cost  # usage_event also batch-priced
        assert int(event.organization_id) == org_id
    finally:
        db.close()


def test_batch_spend_without_org_still_writes_call_log(session_factory):
    """No org context: the usage_event is skipped (it needs an org) but a
    call_log row is STILL written (org nullable) so the spend reconciles —
    this is the exact hole that dropped batch tokens and produced the -77%."""
    submission = types.SimpleNamespace(
        batch_id="batch_y",
        metering_by_custom_id={},  # no attribution for this custom_id
    )

    runner_batch._record_batch_spend(
        submission, "orphan", message=_fake_message(in_tok=2000, out_tok=100), ctx=_ctx(in_tok=2000, out_tok=100)
    )

    db = session_factory()
    try:
        logs = db.query(ClaudeCallLog).all()
        events = db.query(UsageEvent).all()
        assert len(logs) == 1, "spend must be captured even without org attribution"
        assert len(events) == 0, "usage_event needs an org; correctly skipped"
        log = logs[0]
        assert log.organization_id is None
        assert log.usage_event_id is None
        assert log.input_tokens == 2000
        assert log.cost_usd_micro == raw_cost_usd_micro(
            input_tokens=2000, output_tokens=100, model=_MODEL, service_tier="batch"
        )
    finally:
        db.close()
