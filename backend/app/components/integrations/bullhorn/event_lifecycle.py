"""Crash-safe Bullhorn event-subscription and destructive-poll lifecycle.

Bullhorn caps subscriptions per database and a normal event GET consumes its
batch remotely before our database can checkpoint the returned request id. The
helpers make both remote mutations recoverable: subscription ids are
deterministic, local intent is committed before PUT, and every destructive GET
is preceded by a durable intent anchored to the last locally completed id.
Bullhorn's non-destructive ``lastRequestId`` endpoint is reserved for bootstrap
and crash recovery rather than doubling the normal poll call rate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Literal

from sqlalchemy.orm import Session

from ....models.organization import Organization
from .errors import BullhornApiError
from .event_state import (
    POLL_INTENT_KEY as _POLL_INTENT_KEY,
    SUBSCRIPTION_STATE_KEY as _SUBSCRIPTION_STATE_KEY,
    deployment_namespace as _deployment_namespace,
    durable_poll_intent as _durable_poll_intent,
    event_epoch,
    lock_expected_intent as _lock_expected_intent,
    new_epoch as _new_epoch,
    normalize_request_id,
    poll_intent as _poll_intent,
    poll_intent_epoch,
    set_config_state as _set_config_state,
    subscription_state as _subscription_state,
)
from .event_subscriptions import (
    SubscriptionProvenanceError,
    deterministic_subscription_id,
    ensure_subscription,
    recreate_subscription,
    validate_subscription_provenance,
)
from .event_validation import validate_event_batch
from .service import BullhornService

__all__ = [
    "deterministic_subscription_id",
    "ensure_subscription",
    "event_epoch",
    "poll_intent_epoch",
    "recreate_subscription",
    "SubscriptionProvenanceError",
    "validate_subscription_provenance",
]


class SubscriptionAnchorReset(RuntimeError):
    """Bullhorn's request sequence moved backwards or failed to advance."""


class SubscriptionAnchorGap(RuntimeError):
    """More than one destructive drain advanced the remote request sequence."""


class EventPollSuperseded(RuntimeError):
    """Another worker replaced the durable state for this poll."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _last_request_id(
    client: BullhornService,
    subscription_id: str,
    *,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    guard = provider_guard or (lambda: None)
    guard()
    try:
        payload = client.get_last_request_id(subscription_id=subscription_id)
    except Exception:
        guard()
        raise
    guard()
    value = payload.get("result") if isinstance(payload, dict) else None
    return normalize_request_id(value)


def current_remote_request_id(
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Read a non-destructive recovery anchor for a proven subscription."""
    validate_subscription_provenance(org)
    subscription_id = str(org.bullhorn_event_subscription_id or "").strip()
    return _last_request_id(
        client,
        subscription_id,
        provider_guard=provider_guard,
    )


def recover_poll_intent(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> Literal[
    "none",
    "checkpointed",
    "empty",
    "subscription_dead",
    "subscription_reset",
    "request_sequence_gap",
    "superseded",
]:
    """Recover a possibly consumed batch before allowing another fresh drain."""
    guard = provider_guard or (lambda: None)
    intent = _durable_poll_intent(
        db,
        org,
        validator=validate_subscription_provenance,
    )
    if not intent:
        return "none"
    subscription_id = str(org.bullhorn_event_subscription_id or "").strip()
    if str(intent.get("subscription_id") or "") != subscription_id:
        raise RuntimeError("Bullhorn event-poll intent does not match subscription")
    baseline = str(intent.get("baseline_request_id") or "").strip()
    if not baseline:
        raise RuntimeError("Bullhorn event-poll intent has no recovery anchor")
    baseline = normalize_request_id(baseline)
    intent_epoch = str(intent.get("epoch") or "")
    if intent.get("anchor_reset_detected") is True:
        return "subscription_reset"
    if intent.get("sequence_gap_detected") is True:
        return "request_sequence_gap"
    try:
        latest = _last_request_id(
            client,
            subscription_id,
            provider_guard=provider_guard,
        )
    except BullhornApiError as exc:
        if getattr(exc, "status_code", None) == 404:
            return "subscription_dead"
        raise
    if latest == baseline:
        if _lock_expected_intent(db, org, intent_epoch) is None:
            return "superseded"
        _set_config_state(org, _POLL_INTENT_KEY, None)
        db.add(org)
        guard()
        db.commit()
        return "none"
    if int(latest) < int(baseline):
        current = _lock_expected_intent(db, org, intent_epoch)
        if current is None:
            return "superseded"
        _set_config_state(
            org,
            _POLL_INTENT_KEY,
            {**current, "anchor_reset_detected": True},
        )
        db.add(org)
        guard()
        db.commit()
        return "subscription_reset"
    # Bullhorn does not document request ids as a contiguous per-subscription
    # counter.  The committed single-call intent, per-org mutex and epoch CAS
    # prove which destructive read is being recovered; monotonic advancement is
    # the only provider-id property we rely on.  Refetch remains strict about
    # echoing this exact observed id.
    guard()
    try:
        payload = client.refetch_events(
            subscription_id=subscription_id,
            request_id=latest,
            max_events=100,
        )
    except Exception:
        guard()
        raise
    guard()
    if not isinstance(payload, dict):
        raise RuntimeError("Bullhorn returned an invalid event replay response")
    recovered_events = validate_replay_payload(payload, expected_request_id=latest)
    if _lock_expected_intent(db, org, intent_epoch) is None:
        return "superseded"
    org.bullhorn_event_request_id = latest if recovered_events else None
    if not recovered_events:
        record_completed_request_id(org, latest)
    _set_config_state(org, _POLL_INTENT_KEY, None)
    db.add(org)
    guard()
    db.commit()
    return "checkpointed" if recovered_events else "empty"


def prepare_fresh_poll(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Commit a recovery anchor before the destructive remote GET."""
    guard = provider_guard or (lambda: None)
    db.refresh(org)
    validate_subscription_provenance(org)
    subscription_id = str(org.bullhorn_event_subscription_id or "").strip()
    if not subscription_id:
        raise RuntimeError("Bullhorn event poll requires a subscription")
    initial_state = _subscription_state(org)
    initial_baseline = initial_state.get("last_completed_request_id")
    bootstrap = None
    if not (
        initial_state.get("state") == "active"
        and str(initial_state.get("subscription_id") or "") == subscription_id
        and initial_baseline is not None
    ):
        bootstrap = _last_request_id(
            client,
            subscription_id,
            provider_guard=provider_guard,
        )
    db.refresh(org, with_for_update=True)
    validate_subscription_provenance(org)
    if _poll_intent(org) or org.bullhorn_event_request_id:
        db.rollback()
        raise EventPollSuperseded("Bullhorn event poll state changed")
    state = _subscription_state(org)
    baseline_value = state.get("last_completed_request_id")
    if (
        state.get("state") == "active"
        and str(state.get("subscription_id") or "") == subscription_id
        and baseline_value is not None
    ):
        baseline = normalize_request_id(baseline_value)
    else:
        if bootstrap is None:
            db.rollback()
            raise EventPollSuperseded("Bullhorn event anchor changed")
        baseline = bootstrap
        _set_config_state(
            org,
            _SUBSCRIPTION_STATE_KEY,
            {
                "version": 1,
                "subscription_id": subscription_id,
                "state": "active",
                "environment_namespace": _deployment_namespace(),
                "activated_at": _now_iso(),
                "last_completed_request_id": baseline,
            },
        )
    anchor_epoch = event_epoch(org) or _new_epoch()
    if event_epoch(org) != anchor_epoch:
        _set_config_state(
            org,
            _SUBSCRIPTION_STATE_KEY,
            {**_subscription_state(org), "anchor_epoch": anchor_epoch},
        )
    intent_epoch = _new_epoch()
    _set_config_state(
        org,
        _POLL_INTENT_KEY,
        {
            "version": 1,
            "subscription_id": subscription_id,
            "baseline_request_id": baseline,
            "anchor_epoch": anchor_epoch,
            "epoch": intent_epoch,
            "prepared_at": _now_iso(),
        },
    )
    db.add(org)
    guard()
    db.commit()
    return intent_epoch


def checkpoint_fresh_poll(
    db: Session,
    org: Organization,
    *,
    payload: object,
    has_events: bool,
    expected_intent_epoch: str,
    provider_guard: Callable[[], None] | None = None,
) -> str:
    """Atomically checkpoint the consumed batch and clear its poll intent."""
    if not isinstance(payload, dict):
        raise RuntimeError("Bullhorn returned an invalid event drain response")
    request_id = normalize_request_id(payload.get("requestId"))
    intent = _lock_expected_intent(db, org, expected_intent_epoch)
    if intent is None:
        raise EventPollSuperseded("Bullhorn event poll was superseded")
    subscription_id = str(org.bullhorn_event_subscription_id or "").strip()
    if str(intent.get("subscription_id") or "") != subscription_id:
        db.rollback()
        raise RuntimeError("Bullhorn event response has no durable poll intent")
    baseline = normalize_request_id(intent.get("baseline_request_id"))
    if int(request_id) <= int(baseline):
        _set_config_state(
            org,
            _POLL_INTENT_KEY,
            {**intent, "anchor_reset_detected": True},
        )
        db.add(org)
        if provider_guard is not None:
            provider_guard()
        db.commit()
        raise SubscriptionAnchorReset(
            "Bullhorn event drain did not advance its recovery anchor"
        )
    org.bullhorn_event_request_id = request_id if has_events else None
    if not has_events:
        record_completed_request_id(org, request_id)
    _set_config_state(org, _POLL_INTENT_KEY, None)
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
    return request_id


def record_completed_request_id(org: Organization, request_id: object) -> None:
    """Advance the local baseline in the transaction that completes a batch."""
    subscription_id = str(org.bullhorn_event_subscription_id or "").strip()
    state = _subscription_state(org)
    if str(state.get("subscription_id") or "") != subscription_id:
        if state:
            raise RuntimeError("Bullhorn completed anchor has no subscription state")
        # Upgrade a legacy row only after its existing checkpoint replayed cleanly.
        state = {
            "version": 1,
            "subscription_id": subscription_id,
            "environment_namespace": _deployment_namespace(),
            "activated_at": _now_iso(),
        }
    _set_config_state(
        org,
        _SUBSCRIPTION_STATE_KEY,
        {
            **state,
            "state": "active",
            "anchor_epoch": state.get("anchor_epoch") or _new_epoch(),
            "last_completed_request_id": normalize_request_id(request_id),
            "last_completed_at": _now_iso(),
        },
    )


def invalidate_completed_request_id(org: Organization) -> None:
    """Force one remote re-anchor after a gap sweep supersedes an unsafe id."""
    state = _subscription_state(org)
    if state:
        state.pop("last_completed_request_id", None)
        state.pop("last_completed_at", None)
        state["anchor_epoch"] = _new_epoch()
        _set_config_state(org, _SUBSCRIPTION_STATE_KEY, state)


def finish_poll_gap_recovery(
    db: Session,
    org: Organization,
    *,
    expected_intent_epoch: str,
    reanchor_request_id: object,
    provider_guard: Callable[[], None] | None = None,
) -> bool:
    """Clear an unsafe intent only if no newer worker replaced its epoch."""
    if _lock_expected_intent(db, org, expected_intent_epoch) is None:
        return False
    org.bullhorn_event_request_id = None
    record_completed_request_id(org, reanchor_request_id)
    _set_config_state(org, _POLL_INTENT_KEY, None)
    db.add(org)
    if provider_guard is not None:
        provider_guard()
    db.commit()
    return True


def validate_replay_payload(
    payload: object,
    *,
    expected_request_id: object,
) -> list[object]:
    """Validate that a replay is anchored to exactly the id we requested."""
    _request_id, replay_events = validate_event_batch(
        payload,
        expected_request_id=expected_request_id,
    )
    return replay_events
