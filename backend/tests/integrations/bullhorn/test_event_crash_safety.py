"""Crash and retry contracts for Bullhorn's destructive event queue."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import sessionmaker

from app.components.integrations.bullhorn import event_lifecycle, events
from app.components.integrations.bullhorn.errors import BullhornApiError
from app.components.integrations.bullhorn.sync_service import BullhornSyncLeaseLost
from app.models.organization import Organization
from app.platform.config import settings


def _org(db) -> Organization:
    org = Organization(name="Event crash-safety org")
    db.add(org)
    db.commit()
    return org


def _event(entity_id: int = 41) -> dict:
    return {
        "eventId": f"evt-{entity_id}",
        "eventType": "ENTITY",
        "entityName": "Candidate",
        "entityId": entity_id,
        "entityEventType": "UPDATED",
        "updatedProperties": ["status"],
    }


@dataclass
class ProtocolClient:
    subscribed: bool = False
    last_request_id: int = 0
    last_batch: list[dict] = field(default_factory=list)
    queue: list[dict] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    fail_create_after_apply: bool = False

    def create_subscription(self, *, subscription_id: str, entity_names: list[str]):
        self.calls.append(f"put:{subscription_id}")
        self.subscribed = True
        self.last_request_id = 0
        self.last_batch = []
        if self.fail_create_after_apply:
            self.fail_create_after_apply = False
            raise TimeoutError("ambiguous transport failure")
        return {"subscriptionId": subscription_id, "lastRequestId": 0}

    def get_last_request_id(self, *, subscription_id: str):
        self.calls.append(f"last:{subscription_id}")
        if not self.subscribed:
            raise BullhornApiError("missing", status_code=404)
        return {"result": self.last_request_id}

    def poll_events(self, *, subscription_id: str, max_events: int):
        self.calls.append(f"poll:{subscription_id}")
        if not self.subscribed:
            raise BullhornApiError("missing", status_code=404)
        self.last_request_id += 1
        self.last_batch = list(self.queue[:max_events])
        del self.queue[: len(self.last_batch)]
        return {"requestId": self.last_request_id, "events": list(self.last_batch)}

    def refetch_events(
        self,
        *,
        subscription_id: str,
        request_id: str | int,
        max_events: int,
    ):
        self.calls.append(f"refetch:{subscription_id}:{request_id}")
        assert int(request_id) == self.last_request_id
        return {"requestId": self.last_request_id, "events": list(self.last_batch)}

    def delete_subscription(self, **_kwargs):
        raise AssertionError("production lifecycle must not DELETE a remote id")


def test_subscription_id_is_deterministic_and_cross_environment_namespaced(
    db,
    monkeypatch,
):
    org = _org(db)
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "staging")
    staging = event_lifecycle.deterministic_subscription_id(org)
    assert event_lifecycle.deterministic_subscription_id(org) == staging

    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "production")
    production = event_lifecycle.deterministic_subscription_id(org)
    assert staging != production
    assert staging.endswith(f"-org-{org.id}")
    assert production.endswith(f"-org-{org.id}")


def test_ambiguous_create_retries_same_durable_id_without_allocating_another(
    db,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "staging")
    org = _org(db)
    client = ProtocolClient(fail_create_after_apply=True)

    with pytest.raises(TimeoutError, match="ambiguous"):
        events.ensure_subscription(db, org, client=client)
    intended_id = org.bullhorn_event_subscription_id
    assert intended_id
    assert org.bullhorn_config["event_subscription_lifecycle"]["state"] == "pending"

    recovered_id, created = events.ensure_subscription(db, org, client=client)
    assert (recovered_id, created) == (intended_id, True)
    assert org.bullhorn_config["event_subscription_lifecycle"]["state"] == "active"
    assert [call for call in client.calls if call.startswith("put:")] == [
        f"put:{intended_id}"
    ]


def test_active_marker_commit_failure_recovers_applied_remote_put(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "staging")
    org = _org(db)
    client = ProtocolClient()
    real_commit = db.commit
    commit_count = 0

    def fail_second_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("database unavailable")
        real_commit()

    monkeypatch.setattr(db, "commit", fail_second_commit)
    with pytest.raises(RuntimeError, match="database unavailable"):
        events.ensure_subscription(db, org, client=client)
    db.rollback()
    monkeypatch.setattr(db, "commit", real_commit)
    db.refresh(org)
    intended_id = org.bullhorn_event_subscription_id
    assert client.subscribed is True
    assert org.bullhorn_config["event_subscription_lifecycle"]["state"] == "pending"

    recovered_id, created = events.ensure_subscription(db, org, client=client)
    assert (recovered_id, created) == (intended_id, True)
    assert len([call for call in client.calls if call.startswith("put:")]) == 1


def test_recreate_clears_checkpoint_durably_before_owned_put(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "staging")
    org = _org(db)
    initial = ProtocolClient()
    subscription_id, _ = events.ensure_subscription(db, org, client=initial)
    org.bullhorn_event_request_id = "88"
    org.bullhorn_config = {
        **org.bullhorn_config,
        "event_poll_intent": {
            "subscription_id": subscription_id,
            "baseline_request_id": "87",
        },
    }
    db.commit()

    class InspectingClient(ProtocolClient):
        def create_subscription(self, *, subscription_id: str, entity_names: list[str]):
            db.refresh(org)
            assert org.bullhorn_event_request_id is None
            assert "event_poll_intent" not in org.bullhorn_config
            assert org.bullhorn_config["event_subscription_lifecycle"]["state"] == "pending"
            return super().create_subscription(
                subscription_id=subscription_id,
                entity_names=entity_names,
            )

    replacement = InspectingClient()
    assert events.recreate_subscription(db, org, client=replacement) == subscription_id
    assert replacement.calls == [f"put:{subscription_id}"]


def test_recreate_refuses_unowned_subscription_without_remote_call(db):
    org = _org(db)
    org.bullhorn_event_subscription_id = "some-other-integrations-subscription"
    db.commit()
    client = ProtocolClient()

    with pytest.raises(RuntimeError, match="ownership is not proven"):
        events.recreate_subscription(db, org, client=client)
    assert client.calls == []


def test_foreign_subscription_fails_before_remote_call_or_local_mutation(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "production")
    org = _org(db)
    org.bullhorn_event_subscription_id = "other-product-subscription"
    org.bullhorn_event_request_id = "44"
    org.bullhorn_config = {"unrelated": {"keep": True}}
    db.commit()
    before = (
        org.bullhorn_event_subscription_id,
        org.bullhorn_event_request_id,
        deepcopy(org.bullhorn_config),
    )
    client = ProtocolClient(subscribed=True)

    result = events.poll_and_process_events(db, org, client=client)

    assert result["status"] == "retry_pending"
    assert result["reason"] == "invalid_subscription_provenance"
    assert client.calls == []
    assert (
        org.bullhorn_event_subscription_id,
        org.bullhorn_event_request_id,
        org.bullhorn_config,
    ) == before


def test_cloned_environment_lifecycle_fails_before_remote_call_or_mutation(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "staging")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)
    before = (
        org.bullhorn_event_subscription_id,
        org.bullhorn_event_request_id,
        deepcopy(org.bullhorn_config),
    )
    client.calls.clear()
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "production")

    result = events.poll_and_process_events(db, org, client=client)

    assert result["reason"] == "invalid_subscription_provenance"
    assert client.calls == []
    assert (
        org.bullhorn_event_subscription_id,
        org.bullhorn_event_request_id,
        org.bullhorn_config,
    ) == before


def test_exact_deterministic_id_without_lifecycle_is_not_auto_adopted(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    org.bullhorn_event_subscription_id = event_lifecycle.deterministic_subscription_id(org)
    org.bullhorn_config = None
    db.commit()
    client = ProtocolClient(subscribed=True)

    with pytest.raises(RuntimeError, match="ownership is not proven"):
        events.ensure_subscription(db, org, client=client)
    result = events.poll_and_process_events(db, org, client=client)

    assert result["reason"] == "invalid_subscription_provenance"
    assert client.calls == []
    assert org.bullhorn_config is None


def test_crash_after_poll_intent_before_remote_get_allows_safe_fresh_retry(
    db,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)

    event_lifecycle.prepare_fresh_poll(db, org, client=client)
    assert "event_poll_intent" in org.bullhorn_config
    assert event_lifecycle.recover_poll_intent(db, org, client=client) == "none"
    assert "event_poll_intent" not in org.bullhorn_config
    assert not [call for call in client.calls if call.startswith("refetch:")]


def test_crash_after_remote_drain_recovers_and_processes_before_fresh_get(
    db,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient(queue=[_event()])
    events.ensure_subscription(db, org, client=client)
    event_lifecycle.prepare_fresh_poll(db, org, client=client)
    client.poll_events(
        subscription_id=org.bullhorn_event_subscription_id,
        max_events=events.EVENT_BATCH_SIZE,
    )
    # Simulated process death: response was never checkpointed locally.
    processed: list[int] = []
    monkeypatch.setattr(
        events,
        "dispatch_event",
        lambda _db, _org, event, **_kwargs: processed.append(event["entityId"])
        or "processed",
    )
    client.calls.clear()

    result = events.poll_and_process_events(db, org, client=client)

    assert result["status"] == "ok"
    assert processed == [41]
    first_fresh_poll = next(i for i, call in enumerate(client.calls) if call.startswith("poll:"))
    recovery_refetch = next(i for i, call in enumerate(client.calls) if call.startswith("refetch:"))
    assert recovery_refetch < first_fresh_poll
    assert org.bullhorn_event_request_id is None
    assert "event_poll_intent" not in org.bullhorn_config


def test_stale_worker_cannot_overwrite_newer_completed_anchor(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient(queue=[_event(91)])
    events.ensure_subscription(db, org, client=client)
    stale_epoch = event_lifecycle.prepare_fresh_poll(db, org, client=client)
    stale_payload = client.poll_events(
        subscription_id=org.bullhorn_event_subscription_id,
        max_events=events.EVENT_BATCH_SIZE,
    )
    processed: list[int] = []
    monkeypatch.setattr(
        events,
        "dispatch_event",
        lambda _db, _org, event, **_kwargs: processed.append(event["entityId"])
        or "processed",
    )
    competing_db = sessionmaker(bind=db.get_bind())()
    try:
        competing_org = competing_db.get(Organization, org.id)
        assert (
            events.poll_and_process_events(competing_db, competing_org, client=client)[
                "status"
            ]
            == "ok"
        )
    finally:
        competing_db.close()

    with pytest.raises(event_lifecycle.EventPollSuperseded):
        event_lifecycle.checkpoint_fresh_poll(
            db,
            org,
            payload=stale_payload,
            has_events=True,
            expected_intent_epoch=stale_epoch,
        )
    db.refresh(org)
    assert processed == [91]
    assert org.bullhorn_event_request_id is None
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == "2"


def test_recovery_accepts_monotonic_noncontiguous_provider_request_ids(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient(queue=[_event(92), _event(93)])
    events.ensure_subscription(db, org, client=client)
    event_lifecycle.prepare_fresh_poll(db, org, client=client)
    # Bullhorn does not guarantee a contiguous per-subscription sequence. One
    # destructive read can legitimately move from the baseline to a high id.
    client.last_request_id = 900
    client.poll_events(
        subscription_id=org.bullhorn_event_subscription_id,
        max_events=events.EVENT_BATCH_SIZE,
    )
    client.calls.clear()

    assert event_lifecycle.recover_poll_intent(db, org, client=client) == "checkpointed"
    assert client.calls == [
        f"last:{org.bullhorn_event_subscription_id}",
        f"refetch:{org.bullhorn_event_subscription_id}:901",
    ]
    assert org.bullhorn_event_request_id == "901"
    assert "event_poll_intent" not in org.bullhorn_config


def test_checkpoint_commit_failure_never_processes_until_retry(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient(queue=[_event(73)])
    events.ensure_subscription(db, org, client=client)
    processed: list[int] = []
    monkeypatch.setattr(
        events,
        "dispatch_event",
        lambda _db, _org, event, **_kwargs: processed.append(event["entityId"])
        or "processed",
    )
    real_commit = db.commit
    commit_count = 0

    def fail_checkpoint_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("checkpoint database failure")
        real_commit()

    monkeypatch.setattr(db, "commit", fail_checkpoint_commit)
    with pytest.raises(RuntimeError, match="checkpoint database failure"):
        events.poll_and_process_events(db, org, client=client)
    assert processed == []
    db.rollback()
    monkeypatch.setattr(db, "commit", real_commit)
    db.refresh(org)

    result = events.poll_and_process_events(db, org, client=client)
    assert result["status"] == "ok"
    assert processed == [73]


def test_lease_lost_after_destructive_drain_preserves_intent_without_second_call(
    db,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    lost = {"value": False}

    class LosingClient(ProtocolClient):
        def poll_events(self, *, subscription_id: str, max_events: int):
            payload = super().poll_events(
                subscription_id=subscription_id,
                max_events=max_events,
            )
            lost["value"] = True
            return payload

    client = LosingClient(queue=[_event(74)])
    events.ensure_subscription(db, org, client=client)
    client.calls.clear()

    def _guard():
        if lost["value"]:
            raise BullhornSyncLeaseLost()

    with pytest.raises(BullhornSyncLeaseLost):
        events.poll_and_process_events(
            db,
            org,
            client=client,
            provider_guard=_guard,
        )

    assert client.calls == [f"poll:{org.bullhorn_event_subscription_id}"]
    assert "event_poll_intent" in org.bullhorn_config
    assert org.bullhorn_event_request_id is None
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == "0"


def test_lease_lost_between_events_keeps_whole_batch_checkpointed(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient(queue=[_event(75), _event(76)])
    events.ensure_subscription(db, org, client=client)
    lost = {"value": False}
    processed: list[int] = []

    def _dispatch(_db, _org, event, **_kwargs):
        processed.append(event["entityId"])
        lost["value"] = True
        return "processed"

    def _guard():
        if lost["value"]:
            raise BullhornSyncLeaseLost()

    monkeypatch.setattr(events, "dispatch_event", _dispatch)

    with pytest.raises(BullhornSyncLeaseLost):
        events.poll_and_process_events(
            db,
            org,
            client=client,
            provider_guard=_guard,
        )

    assert processed == [75]
    assert org.bullhorn_event_request_id == "1"
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == "0"


def test_normal_empty_poll_uses_no_last_request_api_call(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)
    client.calls.clear()

    result = events.poll_and_process_events(db, org, client=client)

    assert result["status"] == "ok"
    assert client.calls == [f"poll:{org.bullhorn_event_subscription_id}"]
    lifecycle = org.bullhorn_config["event_subscription_lifecycle"]
    assert lifecycle["last_completed_request_id"] == "1"


@pytest.mark.parametrize(
    "bad_request_id",
    [True, 1.5, "", "abc", "１２", "1\n2"],
)
def test_malformed_fresh_request_id_fails_closed_without_echo(
    db,
    monkeypatch,
    bad_request_id,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)

    class MalformedClient(ProtocolClient):
        corrupt = True

        def poll_events(self, *, subscription_id: str, max_events: int):
            payload = super().poll_events(
                subscription_id=subscription_id,
                max_events=max_events,
            )
            if self.corrupt:
                self.corrupt = False
                payload["requestId"] = bad_request_id
            return payload

    client = MalformedClient(queue=[_event()])
    events.ensure_subscription(db, org, client=client)
    processed: list[int] = []
    monkeypatch.setattr(
        events,
        "dispatch_event",
        lambda _db, _org, event, **_kwargs: processed.append(event["entityId"])
        or "processed",
    )

    first = events.poll_and_process_events(db, org, client=client)

    assert first["status"] == "retry_pending"
    assert first["reason"] == "invalid_event_batch"
    assert processed == []
    assert "event_poll_intent" in org.bullhorn_config
    assert org.bullhorn_event_request_id is None

    second = events.poll_and_process_events(db, org, client=client)

    assert second["status"] == "ok"
    assert processed == [41]
    assert "event_poll_intent" not in org.bullhorn_config
    assert org.bullhorn_event_request_id is None


@pytest.mark.parametrize(
    "malformation",
    [
        "missing_events",
        "null_events",
        "string_events",
        "mixed_events",
        "unknown_entity",
        "boolean_entity_id",
        "zero_entity_id",
        "unicode_entity_id",
        "malformed_event_type",
        "string_updated_properties",
    ],
)
def test_malformed_fresh_envelope_retries_exact_consumed_batch_before_advancing(
    db,
    monkeypatch,
    malformation,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)

    class MalformedOnceClient(ProtocolClient):
        corrupt = True

        def poll_events(self, *, subscription_id: str, max_events: int):
            payload = super().poll_events(
                subscription_id=subscription_id,
                max_events=max_events,
            )
            if not self.corrupt:
                return payload
            self.corrupt = False
            event = dict(payload["events"][0])
            payload["events"] = [event]
            if malformation == "missing_events":
                payload.pop("events")
            elif malformation == "null_events":
                payload["events"] = None
            elif malformation == "string_events":
                payload["events"] = "not-a-list"
            elif malformation == "mixed_events":
                payload["events"] = [event, "not-an-event"]
            elif malformation == "unknown_entity":
                event["entityName"] = "Corporation"
            elif malformation == "boolean_entity_id":
                event["entityId"] = True
            elif malformation == "zero_entity_id":
                event["entityId"] = 0
            elif malformation == "unicode_entity_id":
                event["entityId"] = "４１"
            elif malformation == "malformed_event_type":
                event.pop("entityEventType", None)
                event["eventType"] = "UNKNOWN"
            elif malformation == "string_updated_properties":
                event["updatedProperties"] = "status"
            return payload

    client = MalformedOnceClient(queue=[_event()])
    events.ensure_subscription(db, org, client=client)
    baseline = org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ]
    processed: list[int] = []
    monkeypatch.setattr(
        events,
        "dispatch_event",
        lambda _db, _org, event, **_kwargs: processed.append(event["entityId"])
        or "processed",
    )

    first = events.poll_and_process_events(db, org, client=client)

    assert first["reason"] == "invalid_event_batch"
    assert processed == []
    assert org.bullhorn_event_request_id is None
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == baseline
    assert "event_poll_intent" in org.bullhorn_config

    second = events.poll_and_process_events(db, org, client=client)

    assert second["status"] == "ok"
    assert processed == [41]
    refetch_index = next(i for i, call in enumerate(client.calls) if call.startswith("refetch:"))
    later_polls = [i for i, call in enumerate(client.calls) if call.startswith("poll:")]
    assert refetch_index < later_polls[-1]
    assert org.bullhorn_event_request_id is None


def test_conflicting_replay_anchor_fails_closed_and_keeps_intent(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)

    class ConflictingClient(ProtocolClient):
        def refetch_events(self, **kwargs):
            super().refetch_events(**kwargs)
            return {"requestId": self.last_request_id + 1, "events": list(self.last_batch)}

    client = ConflictingClient(queue=[_event()])
    events.ensure_subscription(db, org, client=client)
    event_lifecycle.prepare_fresh_poll(db, org, client=client)
    client.poll_events(
        subscription_id=org.bullhorn_event_subscription_id,
        max_events=events.EVENT_BATCH_SIZE,
    )

    with pytest.raises(RuntimeError, match="conflicting event replay anchor"):
        event_lifecycle.recover_poll_intent(db, org, client=client)
    assert "event_poll_intent" in org.bullhorn_config
    assert org.bullhorn_event_request_id is None


@pytest.mark.parametrize(
    "malformation",
    [
        "missing_events",
        "null_events",
        "string_events",
        "mixed_events",
        "bad_entity_id",
        "wrong_entity",
    ],
)
def test_malformed_checkpoint_replay_is_never_acknowledged_and_replays_again(
    db,
    monkeypatch,
    malformation,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)

    class MalformedReplayOnceClient(ProtocolClient):
        corrupt_replay = True

        def refetch_events(self, **kwargs):
            payload = super().refetch_events(**kwargs)
            if not self.corrupt_replay:
                return payload
            self.corrupt_replay = False
            event = dict(payload["events"][0])
            payload["events"] = [event]
            if malformation == "missing_events":
                payload.pop("events")
            elif malformation == "null_events":
                payload["events"] = None
            elif malformation == "string_events":
                payload["events"] = "bad"
            elif malformation == "mixed_events":
                payload["events"] = [event, None]
            elif malformation == "bad_entity_id":
                event["entityId"] = "41\n42"
            elif malformation == "wrong_entity":
                event["entityName"] = "ClientCorporation"
            return payload

    client = MalformedReplayOnceClient(queue=[_event()])
    events.ensure_subscription(db, org, client=client)
    epoch = event_lifecycle.prepare_fresh_poll(db, org, client=client)
    payload = client.poll_events(
        subscription_id=org.bullhorn_event_subscription_id,
        max_events=events.EVENT_BATCH_SIZE,
    )
    event_lifecycle.checkpoint_fresh_poll(
        db,
        org,
        payload=payload,
        has_events=True,
        expected_intent_epoch=epoch,
    )
    checkpoint = org.bullhorn_event_request_id
    baseline = org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ]
    processed: list[int] = []
    monkeypatch.setattr(
        events,
        "dispatch_event",
        lambda _db, _org, event, **_kwargs: processed.append(event["entityId"])
        or "processed",
    )

    first = events.poll_and_process_events(db, org, client=client)

    assert first["reason"] == "invalid_event_batch"
    assert processed == []
    assert org.bullhorn_event_request_id == checkpoint
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == baseline

    second = events.poll_and_process_events(db, org, client=client)

    assert second["status"] == "ok"
    assert processed == [41]
    assert org.bullhorn_event_request_id is None


def test_malformed_stored_checkpoint_is_rejected_before_refetch_without_echo(
    db,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)
    malicious = "provider-secret-id"
    org.bullhorn_event_request_id = malicious
    db.commit()
    client.calls.clear()

    with pytest.raises(RuntimeError) as caught:
        events.poll_and_process_events(db, org, client=client)

    assert malicious not in str(caught.value)
    assert not [call for call in client.calls if call.startswith("refetch:")]


def test_fresh_response_moving_backwards_surfaces_subscription_reset(
    db,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)
    lifecycle = dict(org.bullhorn_config["event_subscription_lifecycle"])
    lifecycle["last_completed_request_id"] = "9"
    org.bullhorn_config = {
        **org.bullhorn_config,
        "event_subscription_lifecycle": lifecycle,
    }
    db.commit()

    result = events.poll_and_process_events(db, org, client=client)

    assert result["status"] == "retry_pending"
    assert result["reason"] == "subscription_reset"
    assert org.bullhorn_config["event_poll_intent"]["anchor_reset_detected"] is True
    assert org.bullhorn_event_request_id is None


def test_recovery_detects_remote_sequence_reset_without_refetch(db, monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)
    lifecycle = dict(org.bullhorn_config["event_subscription_lifecycle"])
    lifecycle["last_completed_request_id"] = "10"
    org.bullhorn_config = {
        **org.bullhorn_config,
        "event_subscription_lifecycle": lifecycle,
    }
    db.commit()
    event_lifecycle.prepare_fresh_poll(db, org, client=client)
    client.last_request_id = 2
    client.calls.clear()

    recovery = event_lifecycle.recover_poll_intent(db, org, client=client)

    assert recovery == "subscription_reset"
    assert client.calls == [f"last:{org.bullhorn_event_subscription_id}"]
    assert "event_poll_intent" in org.bullhorn_config


def test_sequence_reset_requires_gap_and_reconciliation_before_reanchoring(
    db,
    monkeypatch,
):
    from app.components.integrations.bullhorn import incremental_runner

    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)
    client = ProtocolClient()
    events.ensure_subscription(db, org, client=client)
    lifecycle = dict(org.bullhorn_config["event_subscription_lifecycle"])
    lifecycle["last_completed_request_id"] = "9"
    org.bullhorn_config = {
        **org.bullhorn_config,
        "event_subscription_lifecycle": lifecycle,
    }
    db.commit()
    reset_poll = events.poll_and_process_events(db, org, client=client)
    assert reset_poll["reason"] == "subscription_reset"
    calls: list[str] = []
    monkeypatch.setattr(
        incremental_runner,
        "_gap_sweep",
        lambda *_args, **_kwargs: calls.append("sweep")
        or {"status": "ok", "errors": 0},
    )
    monkeypatch.setattr(
        incremental_runner.reconcile,
        "reconcile_counts",
        lambda *_args, **_kwargs: calls.append("reconcile") or {"ok": True},
    )
    client.calls.clear()

    recovered, failed = incremental_runner._recover_poll_gap(
        db,
        org,
        client=client,
        poll=reset_poll,
        result={},
    )

    assert failed is False
    assert recovered["status"] == "ok"
    assert calls == ["sweep", "reconcile"]
    assert client.calls == [
        f"last:{org.bullhorn_event_subscription_id}",
        f"poll:{org.bullhorn_event_subscription_id}",
    ]
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == "2"


def test_unknown_high_checkpoint_gap_recovery_reanchors_before_fresh_poll(
    db,
    monkeypatch,
):
    from app.components.integrations.bullhorn import incremental_runner

    monkeypatch.setattr(settings, "DEPLOYMENT_ENV", "test")
    org = _org(db)

    class UnknownReplayClient(ProtocolClient):
        def refetch_events(self, **_kwargs):
            self.calls.append("refetch:unknown")
            raise BullhornApiError("unknown request", status_code=400)

    client = UnknownReplayClient()
    events.ensure_subscription(db, org, client=client)
    org.bullhorn_event_request_id = "999999"
    db.commit()
    first = events.poll_and_process_events(db, org, client=client)
    assert first["reason"] == "replay_unavailable"
    client.calls.clear()
    monkeypatch.setattr(
        incremental_runner,
        "_gap_sweep",
        lambda *_args, **_kwargs: {"status": "ok", "errors": 0},
    )
    monkeypatch.setattr(
        incremental_runner.reconcile,
        "reconcile_counts",
        lambda *_args, **_kwargs: {"ok": True},
    )

    recovered, failed = incremental_runner._recover_poll_gap(
        db,
        org,
        client=client,
        poll=first,
        result={},
    )

    assert failed is False
    assert recovered["status"] == "ok"
    assert client.calls[0] == f"last:{org.bullhorn_event_subscription_id}"
    assert client.calls[1] == f"poll:{org.bullhorn_event_subscription_id}"
    assert org.bullhorn_config["event_subscription_lifecycle"][
        "last_completed_request_id"
    ] == "1"


def test_legacy_checkpoint_is_not_auto_adopted_or_replayed(
    db,
    monkeypatch,
):
    org = _org(db)
    org.bullhorn_event_subscription_id = f"taali-{org.id}-legacy"
    org.bullhorn_event_request_id = "1"
    org.bullhorn_config = None
    db.commit()
    client = ProtocolClient(
        subscribed=True,
        last_request_id=1,
        last_batch=[_event(81)],
    )
    before = (org.bullhorn_event_request_id, org.bullhorn_config)

    result = events.poll_and_process_events(db, org, client=client)

    assert result["status"] == "retry_pending"
    assert result["reason"] == "invalid_subscription_provenance"
    assert client.calls == []
    assert (org.bullhorn_event_request_id, org.bullhorn_config) == before
