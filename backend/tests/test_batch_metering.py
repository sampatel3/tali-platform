"""Message Batches API metering through MeteredAnthropicClient.

The batch path used to pass through ``__getattr__`` UN-metered — batch
spend would have been invisible to claude_call_log and usage_events,
violating the water-tight metering invariant. These tests pin the new
contract: ``batches.create`` anchors an ``anthropic_batch_jobs`` row,
``batches.results`` writes call_log + usage_event rows priced at the
batch tier (50% of standard), exactly once per batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    MeteringRequiredError,
)
from app.services.pricing_service import Feature, raw_cost_usd_micro
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_PENDING_STATE,
)
from app.services.usage_credit_reservations import reserve_credits

MODEL = "claude-haiku-4-5"


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _FakeMessage:
    usage: _FakeUsage
    model: str = MODEL
    id: str = "msg_batch_result_1"


@dataclass
class _FakeResult:
    type: str = "succeeded"
    message: Optional[_FakeMessage] = None


@dataclass
class _FakeEntry:
    custom_id: str
    result: _FakeResult


@dataclass
class _FakeBatch:
    id: str = "msgbatch_test_1"
    processing_status: str = "in_progress"


class _FakeBatches:
    def __init__(self, *, entries: Optional[list[_FakeEntry]] = None):
        self.entries = entries or []
        self.created_with: Optional[dict] = None

    def create(self, **kwargs: Any) -> _FakeBatch:
        # The wrapper must strip its metering kwarg before the SDK call.
        assert "metering" not in kwargs
        self.created_with = kwargs
        return _FakeBatch()

    def retrieve(self, batch_id: str) -> _FakeBatch:
        return _FakeBatch(id=batch_id, processing_status="ended")

    def results(self, batch_id: str, **_: Any):
        return iter(self.entries)


class _FakeMessagesResource:
    def __init__(self, *, batches: _FakeBatches):
        self.batches = batches


class _FakeAnthropic:
    def __init__(self, *, batches: _FakeBatches):
        self.messages = _FakeMessagesResource(batches=batches)


def _client(db, *, entries=None) -> tuple[MeteredAnthropicClient, _FakeBatches, int]:
    org = Organization(name="O", slug=f"o-batch-{id(db)}")
    db.add(org)
    db.commit()
    fake = _FakeBatches(entries=entries)
    client = MeteredAnthropicClient(
        inner=_FakeAnthropic(batches=fake), organization_id=int(org.id)
    )
    return client, fake, int(org.id)


def _requests(n: int = 2) -> list[dict]:
    return [
        {
            "custom_id": f"cvparse-{i}",
            "params": {"model": MODEL, "max_tokens": 64, "messages": []},
        }
        for i in range(1, n + 1)
    ]


def _entries(n: int = 2, *, tokens=(1000, 500)) -> list[_FakeEntry]:
    return [
        _FakeEntry(
            custom_id=f"cvparse-{i}",
            result=_FakeResult(
                message=_FakeMessage(
                    usage=_FakeUsage(
                        input_tokens=tokens[0], output_tokens=tokens[1]
                    ),
                    id=f"msg_result_{i}",
                )
            ),
        )
        for i in range(1, n + 1)
    ]


def test_batch_create_records_anchor_row(db):
    client, fake, org_id = _client(db)
    by_custom_id = {
        "cvparse-1": {"entity_id": "application:1", "role_id": 7},
        "cvparse-2": {"entity_id": "application:2", "role_id": 7},
    }
    batch = client.messages.batches.create(
        requests=_requests(),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": by_custom_id,
        },
    )
    assert batch.id == "msgbatch_test_1"
    assert fake.created_with is not None and len(fake.created_with["requests"]) == 2

    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.feature == "cv_parse"
    assert row.organization_id == org_id
    assert row.request_count == 2
    assert row.model == MODEL
    assert row.context == by_custom_id
    assert row.metered_at is None


def test_batch_create_requires_feature(db):
    client, _, _ = _client(db)
    with pytest.raises(MeteringRequiredError):
        client.messages.batches.create(
            requests=_requests(), metering={"organization_id": 1}
        )


def test_batch_results_meter_at_half_price(db):
    entries = _entries(2)
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": "application:1"},
                "cvparse-2": {"entity_id": "application:2"},
            },
        },
    )

    returned = list(client.messages.batches.results("msgbatch_test_1"))
    assert len(returned) == 2  # results still reach the caller

    expected_cost = raw_cost_usd_micro(
        input_tokens=1000, output_tokens=500, model=MODEL, service_tier="batch"
    )
    standard_cost = raw_cost_usd_micro(
        input_tokens=1000, output_tokens=500, model=MODEL
    )
    assert expected_cost * 2 == standard_cost  # sanity: batch is half standard

    logs = db.query(ClaudeCallLog).filter(ClaudeCallLog.model == MODEL).all()
    assert len(logs) == 2
    for log in logs:
        assert log.cost_usd_micro == expected_cost
        assert log.feature_hint == "cv_parse"
        assert log.organization_id == org_id
        assert log.usage_event_id is not None

    events = db.query(UsageEvent).all()
    assert len(events) == 2
    entity_ids = {e.entity_id for e in events}
    assert entity_ids == {"application:1", "application:2"}

    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is not None
    assert row.metered_count == 2
    assert row.status == "ended"


def test_batch_results_idempotent(db):
    entries = _entries(2)
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(),
        metering={"feature": Feature.CV_PARSE, "organization_id": org_id},
    )

    list(client.messages.batches.results("msgbatch_test_1"))
    list(client.messages.batches.results("msgbatch_test_1"))  # poll again

    assert db.query(ClaudeCallLog).count() == 2
    assert db.query(UsageEvent).count() == 2


def test_batch_result_settles_each_request_hold_to_actual(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    entries = _entries(1)
    client, _, org_id = _client(db, entries=entries)
    org = db.get(Organization, org_id)
    org.credits_balance = 100_000
    role = Role(
        organization_id=org_id,
        name="Batch role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()
    reservation = reserve_credits(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        external_ref="usage-hold:batch-result:settle",
        role_id=int(role.id),
        enforce_role_budget=True,
    )
    db.commit()

    client.messages.batches.create(
        requests=_requests(1),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {
                    "entity_id": "application:1",
                    "role_id": int(role.id),
                    "credit_reservation": reservation.as_metering_payload(),
                }
            },
        },
    )
    list(client.messages.batches.results("msgbatch_test_1"))

    event = db.query(UsageEvent).filter_by(organization_id=org_id).one()
    db.refresh(org)
    assert org.credits_balance == 100_000 - int(event.credits_charged)
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert (
        db.query(BillingCreditLedger)
        .filter_by(external_ref="usage-hold:batch-result:settle:settled")
        .count()
        == 1
    )


def test_batch_result_meter_failure_keeps_durable_success_receipt(
    db, monkeypatch,
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    client, _, org_id = _client(db, entries=_entries(1))
    org = db.get(Organization, org_id)
    org.credits_balance = 100_000
    role = Role(
        organization_id=org_id,
        name="Batch receipt role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()
    reservation = reserve_credits(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        external_ref="usage-hold:batch-result:meter-down",
        role_id=int(role.id),
        enforce_role_budget=True,
    )
    db.commit()
    client.messages.batches.create(
        requests=_requests(1),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {
                    "entity_id": "application:1",
                    "role_id": int(role.id),
                    "credit_reservation": reservation.as_metering_payload(),
                }
            },
        },
    )

    with patch.object(client._messages, "_write_event", return_value=None):
        list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter_by(external_ref=reservation.external_ref)
        .one()
    )
    receipt = hold.entry_metadata["deferred_usage_event"]
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    assert receipt["input_tokens"] == 1_000
    assert receipt["output_tokens"] == 500
    assert receipt["service_tier"] == "batch"
    assert receipt["role_id"] == int(role.id)
    assert db.query(UsageEvent).count() == 0
    assert (
        db.query(AnthropicBatchJob)
        .filter_by(batch_id="msgbatch_test_1")
        .one()
        .metered_at
        is None
    )


def test_ambiguous_batch_submit_failure_retains_request_hold(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    client, fake, org_id = _client(db)
    org = db.get(Organization, org_id)
    org.credits_balance = 100_000
    role = Role(organization_id=org_id, name="Ambiguous batch role")
    db.add(role)
    db.commit()
    reservation = reserve_credits(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        external_ref="usage-hold:batch-submit:ambiguous",
        role_id=int(role.id),
        enforce_role_budget=True,
    )
    db.commit()
    fake.create = MagicMock(side_effect=TimeoutError("batch submit timed out"))

    with pytest.raises(TimeoutError):
        client.messages.batches.create(
            requests=_requests(1),
            metering={
                "feature": Feature.CV_PARSE,
                "organization_id": org_id,
                "by_custom_id": {
                    "cvparse-1": {
                        "organization_id": org_id,
                        "role_id": int(role.id),
                        "credit_reservation": reservation.as_metering_payload(),
                    }
                },
            },
        )

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == reservation.external_ref)
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
    assert db.get(Organization, org_id).credits_balance < 100_000
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .count()
        == 1
    )


def test_non_succeeded_batch_result_releases_request_hold(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    entries = [_FakeEntry(custom_id="cvparse-1", result=_FakeResult(type="errored"))]
    client, _, org_id = _client(db, entries=entries)
    org = db.get(Organization, org_id)
    org.credits_balance = 100_000
    role = Role(
        organization_id=org_id,
        name="Failed batch role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()
    reservation = reserve_credits(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        external_ref="usage-hold:batch-result:release",
        role_id=int(role.id),
        enforce_role_budget=True,
    )
    db.commit()

    client.messages.batches.create(
        requests=_requests(1),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {
                    "role_id": int(role.id),
                    "credit_reservation": reservation.as_metering_payload(),
                }
            },
        },
    )
    list(client.messages.batches.results("msgbatch_test_1"))

    db.refresh(org)
    assert org.credits_balance == 100_000
    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 0
    assert (
        db.query(BillingCreditLedger)
        .filter_by(external_ref="usage-hold:batch-result:release:settled")
        .count()
        == 1
    )


def test_partial_batch_meter_retry_reuses_settled_request_events(db, monkeypatch):
    """A failed later call-log write must not double-count earlier results."""

    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    client, _, org_id = _client(db, entries=_entries(2))
    org = db.get(Organization, org_id)
    org.credits_balance = 100_000
    role = Role(
        organization_id=org_id,
        name="Retry-safe batch role",
        monthly_usd_budget_cents=100,
    )
    db.add(role)
    db.commit()
    reservations = {}
    for i in (1, 2):
        reservations[f"cvparse-{i}"] = reserve_credits(
            db,
            organization_id=org_id,
            feature=Feature.CV_PARSE,
            external_ref=f"usage-hold:batch-result:retry-{i}",
            role_id=int(role.id),
            enforce_role_budget=True,
        )
    db.commit()
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                custom_id: {
                    "entity_id": f"application:{i}",
                    "role_id": int(role.id),
                    "credit_reservation": reservation.as_metering_payload(),
                }
                for i, (custom_id, reservation) in enumerate(
                    reservations.items(), start=1
                )
            },
        },
    )

    real_log_write = client._messages._record_call_log_safe
    attempts = 0

    def _fail_second_log(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return False
        return real_log_write(**kwargs)

    with patch.object(
        client._messages,
        "_record_call_log_safe",
        side_effect=_fail_second_log,
    ):
        list(client.messages.batches.results("msgbatch_test_1"))

    first_balance = int(db.get(Organization, org_id).credits_balance)
    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 2
    assert db.query(ClaudeCallLog).count() == 1
    batch_row = (
        db.query(AnthropicBatchJob)
        .filter_by(batch_id="msgbatch_test_1")
        .one()
    )
    assert batch_row.metered_at is None

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 2
    assert db.query(ClaudeCallLog).count() == 2
    assert int(db.get(Organization, org_id).credits_balance) == first_balance


def test_unknown_batch_results_still_capture_call_log(db):
    """A batch submitted outside the wrapper still lands call_log rows
    (Feature.OTHER, no org) so reconciliation against Anthropic stays tight."""
    entries = _entries(1)
    client, _, _ = _client(db, entries=entries)

    list(client.messages.batches.results("msgbatch_unknown_9"))

    logs = db.query(ClaudeCallLog).all()
    assert len(logs) == 1
    assert logs[0].feature_hint == "other"
    assert logs[0].organization_id is None
    assert logs[0].usage_event_id is None  # no org → no usage_event
    assert db.query(UsageEvent).count() == 0

    # And the stub anchor row latches idempotency for repeat polls.
    list(client.messages.batches.results("msgbatch_unknown_9"))
    assert db.query(ClaudeCallLog).count() == 1


def test_failed_entries_are_not_billed(db):
    entries = _entries(1) + [
        _FakeEntry(custom_id="cvparse-9", result=_FakeResult(type="errored"))
    ]
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(1),
        metering={"feature": Feature.CV_PARSE, "organization_id": org_id},
    )

    returned = list(client.messages.batches.results("msgbatch_test_1"))
    assert len(returned) == 2  # caller still sees the errored entry
    assert db.query(ClaudeCallLog).count() == 1  # but only success is billed

    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_count == 1


def test_retrieve_passes_through(db):
    client, _, _ = _client(db)
    batch = client.messages.batches.retrieve("msgbatch_test_1")
    assert batch.processing_status == "ended"


def test_swallowed_write_failure_does_not_latch(db, monkeypatch):
    """A metering write that fails-and-swallows must NOT set metered_at —
    the next results() call retries the batch instead of permanently
    under-counting (Codex P2 on PR #869)."""
    from app.services import metered_anthropic_client as mac

    entries = _entries(2)
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(),
        metering={"feature": Feature.CV_PARSE, "organization_id": org_id},
    )

    calls = {"n": 0}
    real = mac._MeteredMessages._record_call_log_safe

    def _flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # first write swallowed a failure
        return real(self, **kwargs)

    monkeypatch.setattr(mac._MeteredMessages, "_record_call_log_safe", _flaky)

    list(client.messages.batches.results("msgbatch_test_1"))
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    db.refresh(row)
    assert row.metered_at is None  # not latched

    # Next poll retries and, with writes healthy, latches.
    list(client.messages.batches.results("msgbatch_test_1"))
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is not None
    assert row.metered_count == 2


def test_batch_create_rejects_unknown_feature(db):
    client, _, _ = _client(db)
    with pytest.raises(MeteringRequiredError):
        client.messages.batches.create(
            requests=_requests(), metering={"feature": "not_a_real_feature"}
        )


def test_poll_retrieve_falls_back_to_shared_key():
    """With per-org keys enabled, a batch submitted on the shared-key
    fallback 404s under the org's workspace key — the poll must retry with
    the shared client (Codex P2 on PR #869)."""
    from types import SimpleNamespace

    from app.tasks.anthropic_batch_tasks import _retrieve_with_key_fallback

    class _NotFound(Exception):
        status_code = 404

    org_client_calls = []
    shared_client_calls = []

    def _make_client(*, calls, fail):
        def _retrieve(batch_id):
            calls.append(batch_id)
            if fail:
                raise _NotFound()
            return SimpleNamespace(processing_status="ended")

        return SimpleNamespace(
            messages=SimpleNamespace(
                batches=SimpleNamespace(retrieve=_retrieve)
            )
        )

    def _get_metered_client(*, organization_id=None):
        if organization_id is not None:
            return _make_client(calls=org_client_calls, fail=True)
        return _make_client(calls=shared_client_calls, fail=False)

    row = SimpleNamespace(batch_id="msgbatch_x", organization_id=2)
    client, batch = _retrieve_with_key_fallback(_get_metered_client, row)
    assert batch.processing_status == "ended"
    assert org_client_calls == ["msgbatch_x"]
    assert shared_client_calls == ["msgbatch_x"]  # fallback used

    # Non-404 errors propagate — no silent fallback.
    def _get_boom_client(*, organization_id=None):
        def _retrieve(batch_id):
            raise RuntimeError("boom")

        return SimpleNamespace(
            messages=SimpleNamespace(
                batches=SimpleNamespace(retrieve=_retrieve)
            )
        )

    try:
        _retrieve_with_key_fallback(_get_boom_client, row)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
