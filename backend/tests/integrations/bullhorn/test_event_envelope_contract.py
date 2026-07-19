"""Contracts for Bullhorn's official ENTITY event envelope."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.components.integrations.bullhorn import event_handlers, events
from tests.fakes.bullhorn_state import FakeBullhornState, SubscriptionState


class _DB:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


class _Org:
    id = 41


NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


@pytest.mark.parametrize("mutation", ["INSERTED", "UPDATED"])
def test_official_insert_and_update_envelopes_refetch_the_entity(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    seen: list[str] = []

    def handle_candidate(
        _db: object,
        _org: object,
        entity_id: str,
        *,
        client: object,
        now: datetime,
        provider_guard=None,
    ) -> str:
        assert client is not None
        assert now == NOW
        assert provider_guard is None
        seen.append(entity_id)
        return "candidate-upserted"

    monkeypatch.setattr(event_handlers, "_handle_candidate", handle_candidate)
    event = {
        "eventType": "ENTITY",
        "entityEventType": mutation,
        "entityName": "Candidate",
        "entityId": 73,
    }

    assert event_handlers.normalize_event_type(event) == mutation
    assert event_handlers.dispatch_event(
        _DB(),
        _Org(),
        event,
        client=object(),
        now=NOW,
    ) == "candidate-upserted"
    assert seen == ["73"]


def test_entity_event_type_is_authoritative_for_official_delete_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def handle_delete(
        _db: object,
        _org: object,
        entity_name: str,
        entity_id: str,
        *,
        now: datetime,
        provider_guard=None,
    ) -> str:
        assert now == NOW
        assert provider_guard is None
        calls.append((entity_name, entity_id))
        return "deleted"

    monkeypatch.setattr(event_handlers, "_handle_delete", handle_delete)
    monkeypatch.setattr(
        event_handlers,
        "_handle_candidate",
        lambda *_args, **_kwargs: pytest.fail("delete must not take the upsert path"),
    )
    event = {
        "eventType": "ENTITY",
        "entityEventType": "DELETED",
        "entityName": "Candidate",
        "entityId": 73,
    }

    assert event_handlers.normalize_event_type(event) == "DELETED"
    assert event_handlers.dispatch_event(
        _DB(),
        _Org(),
        event,
        client=object(),
        now=NOW,
    ) == "deleted"
    assert calls == [("Candidate", "73")]


@pytest.mark.parametrize("mutation", ["INSERTED", "UPDATED", "DELETED", "DELETE"])
def test_legacy_direct_mutation_envelopes_remain_supported(mutation: str) -> None:
    assert event_handlers.normalize_event_type({"eventType": mutation}) == mutation
    assert event_handlers.normalize_event_type(
        {"eventType": mutation, "entityEventType": mutation}
    ) == mutation


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"eventType": "ENTITY"},
        {"eventType": "ENTITY", "entityEventType": "UNKNOWN"},
        {"eventType": "UNKNOWN", "entityEventType": "UPDATED"},
        {"eventType": "UPDATED", "entityEventType": "DELETED"},
        {"eventType": 123, "entityEventType": "UPDATED"},
    ],
)
def test_malformed_or_conflicting_envelopes_fail_safe(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, object],
) -> None:
    monkeypatch.setattr(
        event_handlers,
        "_handle_candidate",
        lambda *_args, **_kwargs: pytest.fail("malformed event reached a handler"),
    )
    complete_event = {
        "entityName": "Candidate",
        "entityId": 73,
        **event,
    }

    assert event_handlers.normalize_event_type(complete_event) is None
    assert event_handlers.dispatch_event(
        _DB(),
        _Org(),
        complete_event,
        client=object(),
        now=NOW,
    ) == "skipped"


@pytest.mark.parametrize("mutation", ["INSERTED", "UPDATED", "DELETED"])
def test_official_envelope_failure_metrics_use_the_mutation_type(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    monkeypatch.setattr(events, "dispatch_event", lambda *_args, **_kwargs: "error")
    counters = {"events": 0, "errors": 0, "processed": 0}
    event = {
        "eventType": "ENTITY",
        "entityEventType": mutation,
        "entityName": "Candidate",
        "entityId": 73,
    }

    clean, summary = events._process_batch(
        _DB(),
        _Org(),
        [event],
        client=object(),
        counters=counters,
    )

    assert clean is False
    assert summary == {
        "error_count": 1,
        "entity_types": ["Candidate"],
        "event_types": [mutation],
    }


def test_live_fake_emits_the_official_shape() -> None:
    state = FakeBullhornState()
    org = state.make_org("official_event_shape")
    org.subscriptions["capture"] = SubscriptionState(
        sub_id="capture",
        entity_names=["Candidate"],
        event_types=["UPDATED"],
        created_at=state.now,
    )

    state.emit_event(
        org,
        "capture",
        entity_name="Candidate",
        entity_id=73,
        event_type="UPDATED",
        updated_properties=["status"],
    )

    event = org.subscriptions["capture"].queue[0]
    assert event["eventType"] == "ENTITY"
    assert event["entityEventType"] == "UPDATED"
    assert event["entityName"] == "Candidate"
    assert event["entityId"] == 73
    assert event["updatedProperties"] == ["status"]
    assert set(event["eventMetadata"]) == {"PERSON_ID", "TRANSACTION_ID"}
