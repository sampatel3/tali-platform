"""Message Batches API metering through MeteredAnthropicClient.

The batch path used to pass through ``__getattr__`` UN-metered — batch
spend would have been invisible to claude_call_log and usage_events,
violating the water-tight metering invariant. These tests pin the new
contract: ``batches.create`` anchors an ``anthropic_batch_jobs`` row,
``batches.results`` writes call_log + usage_event rows priced at the
batch tier (50% of standard), exactly once per batch.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.models.anthropic_batch_job import AnthropicBatchJob
from app.models.anthropic_batch_result_receipt import (
    AnthropicBatchResultReceipt,
)
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    MeteringRequiredError,
    ProviderAttemptMarkerError,
)
from app.services.pricing_service import Feature, raw_cost_usd_micro
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_PENDING_STATE,
)
from app.services.usage_credit_reservations import reserve_credits
from app.services.usage_metering_service import record_event

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
                    usage=_FakeUsage(input_tokens=tokens[0], output_tokens=tokens[1]),
                    id=f"msg_result_{i}",
                )
            ),
        )
        for i in range(1, n + 1)
    ]


def _entries_with_provider_ids(*provider_ids: str) -> list[_FakeEntry]:
    entries = _entries(len(provider_ids))
    for entry, provider_message_id in zip(entries, provider_ids):
        assert entry.result.message is not None
        entry.result.message.id = provider_message_id
    return entries


def _error_entry(custom_id: str) -> _FakeEntry:
    return _FakeEntry(custom_id=custom_id, result=_FakeResult(type="errored"))


def _add_strict_anchor(
    db,
    *,
    organization_id: int,
    custom_ids: tuple[str, ...],
    request_count: Optional[int] = None,
    batch_id: str = "msgbatch_test_1",
) -> AnthropicBatchJob:
    count = len(custom_ids) if request_count is None else request_count
    context = {
        custom_id: {
            "organization_id": organization_id,
            "entity_id": f"application:{custom_id.removeprefix('cvparse-')}",
        }
        for custom_id in custom_ids
    }
    context["_submission_claim"] = {
        "version": 2,
        "state": "submitted",
        "claim_batch_id": "claim:cv_parse:strict-attribution",
        "request_sha256": "strict-attribution",
        "request_count": count,
        "attempt": 1,
        "attempt_id": "strict-attribution-attempt",
        "provider_batch_id": batch_id,
    }
    row = AnthropicBatchJob(
        batch_id=batch_id,
        organization_id=organization_id,
        feature=Feature.CV_PARSE.value,
        model=MODEL,
        request_count=count,
        status="submitted",
        context=context,
    )
    db.add(row)
    db.commit()
    return row


def _stored_receipts(db, *, batch_id: str) -> dict[str, dict[str, Any]]:
    batch = db.query(AnthropicBatchJob).filter_by(batch_id=batch_id).one()
    rows = (
        db.query(AnthropicBatchResultReceipt)
        .filter_by(batch_job_id=int(batch.id))
        .all()
    )
    return {
        str(row.custom_id): {
            "id": int(row.id),
            "state": str(row.state),
            "result_type": str(row.result_type),
            "usage_event_id": row.usage_event_id,
            "call_log_id": row.call_log_id,
            "provider_message_id": row.provider_message_id,
        }
        for row in rows
    }


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


def test_stale_batch_attempt_cannot_hijack_newer_exact_claim(db):
    client, fake, org_id = _client(db)
    claim = AnthropicBatchJob(
        batch_id="claim:cv_parse:attempt-fence",
        organization_id=org_id,
        feature="cv_parse",
        model=MODEL,
        request_count=1,
        status="submitting",
        context={
            "cvparse-1": {"entity_id": "application:1"},
            "_submission_claim": {
                "version": 2,
                "state": "claimed",
                "attempt_id": "new-attempt",
            },
        },
    )
    db.add(claim)
    db.commit()

    with pytest.raises(ProviderAttemptMarkerError):
        client.messages.batches.create(
            requests=_requests(1),
            metering={
                "feature": Feature.CV_PARSE,
                "organization_id": org_id,
                "by_custom_id": {"cvparse-1": {"entity_id": "application:1"}},
                "submission_claim_batch_id": claim.batch_id,
                "submission_claim_attempt_id": "stale-attempt",
            },
        )

    assert fake.created_with is None
    db.expire_all()
    row = db.get(AnthropicBatchJob, claim.id)
    assert row.status == "submitting"
    assert row.context["_submission_claim"]["state"] == "claimed"
    assert row.context["_submission_claim"]["attempt_id"] == "new-attempt"


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


def test_strict_batch_results_validate_complete_attribution_before_metering(db):
    entries = _entries(1) + [_error_entry("cvparse-2")]
    client, _, org_id = _client(db, entries=entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=("cvparse-1", "cvparse-2"),
    )

    returned = list(client.messages.batches.results("msgbatch_test_1"))

    assert returned == entries
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.status == "ended"
    assert row.metered_at is not None
    assert row.metered_count == 1
    assert set(_stored_receipts(db, batch_id="msgbatch_test_1")) == {
        "cvparse-1",
        "cvparse-2",
    }
    assert "_metered_results" not in row.context


@pytest.mark.parametrize(
    ("case", "expected_ids", "entries", "expected_issue"),
    [
        (
            "missing",
            ("cvparse-1", "cvparse-2", "cvparse-3"),
            _entries(1) + [_error_entry("cvparse-2")],
            "result_count_mismatch",
        ),
        (
            "duplicate",
            ("cvparse-1", "cvparse-2"),
            _entries(1) + [_error_entry("cvparse-1")],
            "duplicate_result_custom_ids",
        ),
        (
            "empty",
            ("cvparse-1", "cvparse-2"),
            _entries(1) + [_error_entry("")],
            "empty_result_custom_id",
        ),
        (
            "extra",
            ("cvparse-1", "cvparse-2"),
            _entries(1) + [_error_entry("cvparse-2")] + [_entries(3)[2]],
            "extra_result_custom_ids",
        ),
        (
            "mismatched",
            ("cvparse-1", "cvparse-2"),
            _entries(1) + [_error_entry("cvparse-3")],
            "missing_result_custom_ids",
        ),
        (
            "empty_provider_message_id",
            ("cvparse-1", "cvparse-2"),
            _entries_with_provider_ids("", "msg_result_2"),
            "empty_succeeded_provider_message_id",
        ),
        (
            "duplicate_provider_message_ids",
            ("cvparse-1", "cvparse-2"),
            _entries_with_provider_ids("msg_result_shared", "msg_result_shared"),
            "duplicate_succeeded_provider_message_ids",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_strict_batch_invalid_result_sets_fail_closed_before_any_entry(
    db,
    case,
    expected_ids,
    entries,
    expected_issue,
):
    client, _, org_id = _client(db, entries=entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=expected_ids,
    )

    with patch.object(
        client._messages,
        "_release_credit_reservation_safe",
    ) as release_reservation:
        returned = list(client.messages.batches.results("msgbatch_test_1"))

    assert returned == entries, case
    release_reservation.assert_not_called()
    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.status == "submitted"
    assert row.metered_at is None
    assert row.metered_count == 0
    assert "_metered_results" not in row.context
    pending = row.context["_result_attribution_validation"]
    assert pending["state"] == "reconciliation_pending"
    assert expected_issue in pending["issues"]
    assert pending["observed_result_count"] == len(entries)
    assert len(pending["expected_custom_ids_sha256"]) == 64
    assert len(pending["observed_results_sha256"]) == 64
    assert len(pending["observed_result_sample"]) <= 20


def test_strict_batch_corrupt_persisted_request_count_fails_closed(db):
    entries = _entries(1) + [_error_entry("cvparse-2")]
    client, _, org_id = _client(db, entries=entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=("cvparse-1", "cvparse-2"),
        request_count=3,
    )

    list(client.messages.batches.results("msgbatch_test_1"))

    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    pending = row.context["_result_attribution_validation"]
    assert pending["state"] == "reconciliation_pending"
    assert "submitted_attribution_count_mismatch" in pending["issues"]
    assert "result_count_mismatch" in pending["issues"]
    assert row.metered_at is None


def test_strict_batch_pending_evidence_blocks_automatic_later_metering(db):
    invalid_entries = _entries(1) + [_error_entry("cvparse-1")]
    client, fake, org_id = _client(db, entries=invalid_entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=("cvparse-1", "cvparse-2"),
    )

    list(client.messages.batches.results("msgbatch_test_1"))
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    first_evidence = row.context["_result_attribution_validation"][
        "observed_results_sha256"
    ]

    fake.entries = _entries(1) + [_error_entry("cvparse-2")]
    list(client.messages.batches.results("msgbatch_test_1"))

    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    pending = row.context["_result_attribution_validation"]
    assert pending["state"] == "reconciliation_pending"
    assert pending["observation_count"] == 2
    assert pending["first_observed_results_sha256"] == first_evidence
    assert "prior_reconciliation_pending" in pending["issues"]
    assert "duplicate_result_custom_ids" in pending["issues"]
    assert "duplicate_result_custom_ids" in pending["first_issues"]
    assert row.status == "submitted"
    assert row.metered_at is None


@pytest.mark.parametrize(
    ("conflicting_entries", "expected_issue"),
    [
        (
            _entries(1) + [_error_entry("cvparse-1")],
            "duplicate_result_custom_ids",
        ),
        (
            [_error_entry("cvparse-1"), _entries(2)[1]],
            "result_outcome_mismatch",
        ),
        (
            _entries_with_provider_ids("msg_result_changed")
            + [_error_entry("cvparse-2")],
            "provider_message_id_mismatch",
        ),
    ],
)
def test_strict_latched_batch_conflict_demotes_before_application(
    db,
    conflicting_entries,
    expected_issue,
):
    initial_entries = _entries(1) + [_error_entry("cvparse-2")]
    client, fake, org_id = _client(db, entries=initial_entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=("cvparse-1", "cvparse-2"),
    )
    list(client.messages.batches.results("msgbatch_test_1"))

    fake.entries = conflicting_entries
    returned = list(client.messages.batches.results("msgbatch_test_1"))

    assert returned == conflicting_entries
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    pending = row.context["_result_attribution_validation"]
    assert expected_issue in pending["issues"]
    assert pending["prior_metered_count"] == 1
    assert row.status == "submitted"
    assert row.metered_at is None
    assert row.metered_count == 1


def test_strict_exact_replay_after_application_keeps_terminal_receipt(db):
    entries = _entries(1) + [_error_entry("cvparse-2")]
    client, _, org_id = _client(db, entries=entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=("cvparse-1", "cvparse-2"),
    )
    list(client.messages.batches.results("msgbatch_test_1"))
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    metered_at = row.metered_at
    context = dict(row.context) if isinstance(row.context, dict) else {}
    context["_result_application"] = {
        "version": 1,
        "state": "applied",
        "summary": {"applied": 1},
    }
    row.context = context
    row.status = "results_applied"
    db.commit()

    returned = list(client.messages.batches.results("msgbatch_test_1"))

    assert returned == entries
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.status == "results_applied"
    assert row.metered_at == metered_at
    assert row.context["_result_attribution_validation"]["state"] == "validated"
    assert row.context["_result_application"]["state"] == "applied"


def test_strict_pending_evidence_tolerates_corrupt_observation_count(db):
    entries = _entries(1) + [_error_entry("cvparse-1")]
    client, _, org_id = _client(db, entries=entries)
    row = _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=("cvparse-1", "cvparse-2"),
    )
    list(client.messages.batches.results("msgbatch_test_1"))
    db.refresh(row)
    context = dict(row.context) if isinstance(row.context, dict) else {}
    evidence = dict(context["_result_attribution_validation"])
    evidence["observation_count"] = "corrupt"
    context["_result_attribution_validation"] = evidence
    row.context = context
    db.commit()

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    pending = row.context["_result_attribution_validation"]
    assert pending["state"] == "reconciliation_pending"
    assert pending["observation_count"] == 1
    assert db.query(UsageEvent).count() == 0


def test_strict_large_invalid_evidence_is_bounded(db):
    entries = _entries(2_001)
    entries[-1].custom_id = "x" * 5_000
    client, _, org_id = _client(db, entries=entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=tuple(f"cvparse-{index}" for index in range(1, 2_001)),
    )

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    pending = row.context["_result_attribution_validation"]
    assert pending["state"] == "reconciliation_pending"
    assert pending["observed_result_count"] == 2_001
    assert len(pending["observed_result_sample"]) == 20
    assert max(map(len, pending["extra_custom_id_sample"])) <= 200
    assert len(json.dumps(pending)) < 10_000
    assert "expected_custom_ids" not in pending
    assert "observed_results" not in pending


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


def test_legacy_duplicate_provider_ids_fail_closed_before_entry_work(db):
    entries = _entries_with_provider_ids("msg_shared", "msg_shared")
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(2),
        metering={"feature": Feature.CV_PARSE, "organization_id": org_id},
    )

    with patch.object(client._messages, "_mark_provider_success") as mark_success:
        list(client.messages.batches.results("msgbatch_test_1"))

    mark_success.assert_not_called()
    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is None
    assert _stored_receipts(db, batch_id="msgbatch_test_1") == {}


def test_provider_message_id_replay_across_batches_fails_before_billing(db):
    first_entries = _entries(1)
    client, fake, first_org_id = _client(db, entries=first_entries)
    client.messages.batches.create(
        requests=_requests(1),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": first_org_id,
            "by_custom_id": {"cvparse-1": {"entity_id": "application:1"}},
        },
    )
    list(client.messages.batches.results("msgbatch_test_1"))

    second_org = Organization(name="O2", slug=f"o-batch-replay-{id(db)}")
    db.add(second_org)
    db.commit()
    replay = _entries_with_provider_ids("msg_result_1")[0]
    replay.custom_id = "cvparse-2"
    fake.entries = [replay]
    _add_strict_anchor(
        db,
        organization_id=int(second_org.id),
        custom_ids=("cvparse-2",),
        batch_id="msgbatch_test_2",
    )

    with patch.object(client._messages, "_mark_provider_success") as mark_success:
        returned = list(client.messages.batches.results("msgbatch_test_2"))

    assert returned == [replay]
    mark_success.assert_not_called()
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1
    assert _stored_receipts(db, batch_id="msgbatch_test_2") == {}
    db.expire_all()
    second_row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_2").one()
    assert second_row.metered_at is None
    assert second_row.status == "submitted"
    pending = second_row.context["_result_attribution_validation"]
    assert pending["state"] == "reconciliation_pending"
    assert "provider_message_id_replay" in pending["issues"]


def test_existing_log_consumes_anonymous_legacy_event_exactly_once(db):
    entries = _entries(2)
    # Put the result without a legacy log first. The preflight must reserve the
    # anonymous event for the later result before iteration order can reuse it.
    entries = [entries[1], entries[0]]
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": "application:shared"},
                "cvparse-2": {"entity_id": "application:shared"},
            },
        },
    )
    legacy_event = record_event(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        model=MODEL,
        input_tokens=1_000,
        output_tokens=500,
        service_tier="batch",
        entity_id="application:shared",
        metadata={"batch_id": "msgbatch_test_1"},
    )
    legacy_log = client._messages._build_call_log_row(
        organization_id=org_id,
        model=MODEL,
        usage=entries[1].result.message.usage,
        feature_hint=Feature.CV_PARSE.value,
        status="ok",
        error_reason=None,
        anthropic_request_id="msg_result_1",
        usage_event_id=int(legacy_event.id),
        service_tier="batch",
    )
    db.add(legacy_log)
    db.commit()

    list(client.messages.batches.results("msgbatch_test_1"))

    receipts = _stored_receipts(db, batch_id="msgbatch_test_1")
    assert set(receipts) == {"cvparse-1", "cvparse-2"}
    assert len({receipt["usage_event_id"] for receipt in receipts.values()}) == 2
    assert receipts["cvparse-1"]["usage_event_id"] == int(legacy_event.id)
    assert db.query(UsageEvent).count() == 2
    assert db.query(ClaudeCallLog).count() == 2


def test_partial_legacy_json_receipts_mix_safely_with_normalized_receipts(db):
    entries = [_error_entry("cvparse-1"), _error_entry("cvparse-2")]
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": "application:1"},
                "cvparse-2": {"entity_id": "application:2"},
            },
        },
    )
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    context = dict(row.context)
    context["_metered_results"] = {
        "cvparse-1": {"state": "skipped", "result_type": "errored"}
    }
    row.context = context
    db.commit()

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is not None
    assert row.metered_count == 0
    assert row.context["_metered_results"] == {
        "cvparse-1": {"state": "skipped", "result_type": "errored"}
    }
    assert set(_stored_receipts(db, batch_id="msgbatch_test_1")) == {"cvparse-2"}


def test_conflicting_legacy_and_normalized_receipt_evidence_fails_closed(db):
    entries = _entries(1)
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(1),
        metering={"feature": Feature.CV_PARSE, "organization_id": org_id},
    )
    list(client.messages.batches.results("msgbatch_test_1"))
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    normalized = _stored_receipts(db, batch_id="msgbatch_test_1")["cvparse-1"]
    context = dict(row.context) if isinstance(row.context, dict) else {}
    context["_metered_results"] = {
        "cvparse-1": {
            "state": "metered",
            "result_type": "succeeded",
            "usage_event_id": int(normalized["usage_event_id"]) + 99,
            "call_log_id": normalized["call_log_id"],
            "provider_message_id": normalized["provider_message_id"],
        }
    }
    row.context = context
    row.metered_at = None
    row.status = "submitted"
    db.commit()

    with patch.object(client._messages, "_mark_provider_success") as mark_success:
        list(client.messages.batches.results("msgbatch_test_1"))

    mark_success.assert_not_called()
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1
    db.refresh(row)
    assert row.metered_at is None
    assert _stored_receipts(db, batch_id="msgbatch_test_1")["cvparse-1"] == normalized


def test_large_strict_batch_uses_normalized_receipts_and_bounded_sessions(db):
    """Receipt storage and transaction setup stay linear as batches grow."""

    from app.services import anthropic_batch_result_metering

    entry_count = 64
    entries = _entries(entry_count)
    client, _, org_id = _client(db, entries=entries)
    _add_strict_anchor(
        db,
        organization_id=org_id,
        custom_ids=tuple(f"cvparse-{index}" for index in range(1, entry_count + 1)),
    )
    real_session_local = anthropic_batch_result_metering.SessionLocal
    opened_sessions = 0

    def _counted_session_local():
        nonlocal opened_sessions
        opened_sessions += 1
        return real_session_local()

    with patch.object(
        anthropic_batch_result_metering,
        "SessionLocal",
        _counted_session_local,
    ):
        list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    receipts = (
        db.query(AnthropicBatchResultReceipt).filter_by(batch_job_id=int(row.id)).all()
    )
    assert len(receipts) == entry_count
    assert {receipt.custom_id for receipt in receipts} == {
        f"cvparse-{index}" for index in range(1, entry_count + 1)
    }
    assert {receipt.state for receipt in receipts} == {"metered"}
    assert "_metered_results" not in row.context
    assert row.metered_count == entry_count
    assert opened_sessions <= 2


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
    db,
    monkeypatch,
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

    with patch(
        "app.batch_metering.result_processing.record_event",
        side_effect=RuntimeError("usage event write unavailable"),
    ):
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

    real_log_build = client._messages._build_call_log_row
    attempts = 0

    def _fail_second_log(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("call log write unavailable")
        return real_log_build(**kwargs)

    with patch.object(
        client._messages,
        "_build_call_log_row",
        side_effect=_fail_second_log,
    ):
        list(client.messages.batches.results("msgbatch_test_1"))

    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 1
    assert db.query(ClaudeCallLog).count() == 1
    batch_row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert batch_row.metered_at is None

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    events = db.query(UsageEvent).filter_by(organization_id=org_id).all()
    assert len(events) == 2
    assert db.query(ClaudeCallLog).count() == 2
    assert int(db.get(Organization, org_id).credits_balance) == 100_000 - sum(
        int(event.credits_charged) for event in events
    )


def test_shadow_batch_partial_failure_retry_has_exactly_one_event_per_result(
    db,
    monkeypatch,
):
    """Shadow reservations have no ledger row, so the batch receipt must dedupe."""

    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", False
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", False
    )
    client, _, org_id = _client(db, entries=_entries(2))
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": "application:1"},
                "cvparse-2": {"entity_id": "application:2"},
            },
        },
    )

    real_log_build = client._messages._build_call_log_row
    attempts = 0

    def _fail_second_log(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("call log write unavailable")
        return real_log_build(**kwargs)

    with patch.object(
        client._messages,
        "_build_call_log_row",
        side_effect=_fail_second_log,
    ):
        list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is None
    assert set(_stored_receipts(db, batch_id="msgbatch_test_1")) == {"cvparse-1"}
    assert "_metered_results" not in row.context
    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 1
    assert db.query(ClaudeCallLog).count() == 1

    list(client.messages.batches.results("msgbatch_test_1"))
    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is not None
    assert row.metered_count == 2
    assert set(_stored_receipts(db, batch_id="msgbatch_test_1")) == {
        "cvparse-1",
        "cvparse-2",
    }
    assert "_metered_results" not in row.context
    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 2
    assert db.query(ClaudeCallLog).count() == 2


def test_rolled_back_savepoint_does_not_leak_transient_log_to_next_result(db):
    entries = _entries(2)
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": "application:1"},
                "cvparse-2": {"entity_id": "application:2"},
            },
        },
    )

    from app.batch_metering import result_processing

    real_add_receipt = result_processing.add_receipt
    attempts = 0

    def _fail_first_receipt(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("receipt insert unavailable")
        return real_add_receipt(*args, **kwargs)

    with patch.object(
        result_processing,
        "add_receipt",
        _fail_first_receipt,
    ):
        list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    assert set(_stored_receipts(db, batch_id="msgbatch_test_1")) == {"cvparse-2"}
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is not None
    assert set(_stored_receipts(db, batch_id="msgbatch_test_1")) == {
        "cvparse-1",
        "cvparse-2",
    }
    assert db.query(UsageEvent).count() == 2
    assert db.query(ClaudeCallLog).count() == 2


def test_anonymous_legacy_usage_event_is_consumed_by_only_one_result(db):
    entries = _entries(2)
    client, _, org_id = _client(db, entries=entries)
    shared_entity_id = "application:shared"
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": shared_entity_id},
                "cvparse-2": {"entity_id": shared_entity_id},
            },
        },
    )
    legacy_event = record_event(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        model=MODEL,
        input_tokens=1_000,
        output_tokens=500,
        service_tier="batch",
        entity_id=shared_entity_id,
        metadata={"batch_id": "msgbatch_test_1"},
    )
    db.commit()

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    receipts = _stored_receipts(db, batch_id="msgbatch_test_1")
    assert set(receipts) == {"cvparse-1", "cvparse-2"}
    assert len({receipt["usage_event_id"] for receipt in receipts.values()}) == 2
    assert int(legacy_event.id) in {
        int(receipt["usage_event_id"]) for receipt in receipts.values()
    }
    assert db.query(UsageEvent).count() == 2
    assert db.query(ClaudeCallLog).count() == 2


def test_mismatched_anonymous_legacy_event_fails_closed_without_double_bill(db):
    entries = _entries(1)
    client, _, org_id = _client(db, entries=entries)
    client.messages.batches.create(
        requests=_requests(1),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {"cvparse-1": {"entity_id": "application:1"}},
        },
    )
    record_event(
        db,
        organization_id=org_id,
        feature=Feature.CV_PARSE,
        model=MODEL,
        input_tokens=7,
        output_tokens=3,
        service_tier="batch",
        entity_id="application:1",
        metadata={"batch_id": "msgbatch_test_1"},
    )
    db.commit()

    list(client.messages.batches.results("msgbatch_test_1"))

    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 0
    assert _stored_receipts(db, batch_id="msgbatch_test_1") == {}
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is None


def test_stale_duplicate_poller_reuses_per_result_receipts(db):
    """A worker that missed the final latch still observes committed receipts."""

    client, _, org_id = _client(db, entries=_entries(2))
    client.messages.batches.create(
        requests=_requests(2),
        metering={
            "feature": Feature.CV_PARSE,
            "organization_id": org_id,
            "by_custom_id": {
                "cvparse-1": {"entity_id": "application:1"},
                "cvparse-2": {"entity_id": "application:2"},
            },
        },
    )
    list(client.messages.batches.results("msgbatch_test_1"))

    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    receipts = _stored_receipts(db, batch_id="msgbatch_test_1")
    row.metered_at = None
    row.status = "submitted"
    db.commit()

    list(client.messages.batches.results("msgbatch_test_1"))

    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert _stored_receipts(db, batch_id="msgbatch_test_1") == receipts
    assert "_metered_results" not in row.context
    assert row.metered_at is not None
    assert db.query(UsageEvent).filter_by(organization_id=org_id).count() == 2
    assert db.query(ClaudeCallLog).count() == 2


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
    real = mac._MeteredMessages._build_call_log_row

    def _flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("call log write unavailable")
        return real(self, **kwargs)

    monkeypatch.setattr(mac._MeteredMessages, "_build_call_log_row", _flaky)

    list(client.messages.batches.results("msgbatch_test_1"))
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    db.refresh(row)
    assert row.metered_at is None  # not latched
    assert db.query(UsageEvent).count() == 1
    assert db.query(ClaudeCallLog).count() == 1

    # Next poll retries and, with writes healthy, latches.
    list(client.messages.batches.results("msgbatch_test_1"))
    db.expire_all()
    row = db.query(AnthropicBatchJob).filter_by(batch_id="msgbatch_test_1").one()
    assert row.metered_at is not None
    assert row.metered_count == 2
    assert db.query(UsageEvent).count() == 2
    assert db.query(ClaudeCallLog).count() == 2


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
            messages=SimpleNamespace(batches=SimpleNamespace(retrieve=_retrieve))
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
            messages=SimpleNamespace(batches=SimpleNamespace(retrieve=_retrieve))
        )

    try:
        _retrieve_with_key_fallback(_get_boom_client, row)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
