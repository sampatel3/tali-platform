"""Crash-safe polling for Bullhorn's durable, destructive event queue.

Every fresh drain has a committed intent and every consumed batch is durably
checkpointed before its dirty-flag events are processed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ....models.organization import Organization
from . import event_lifecycle
from .errors import BullhornApiError
from .event_checkpoints import (
    POISON_EVENT_RETRY_LIMIT,
    batch_fingerprint as _batch_fingerprint,
    clear_checkpoint as _clear_checkpoint,
    clear_checkpoint_after_gap_recovery,
    record_poison_failure as _record_poison_failure,
)
from .event_handlers import SUBSCRIBED_ENTITIES, dispatch_event, normalize_event_type
from .event_validation import InvalidEventBatch, validate_event_batch
from .service import BullhornService

logger = logging.getLogger("taali.bullhorn.events")
EVENT_BATCH_SIZE = 100
MAX_BATCHES_PER_CYCLE = 20

__all__ = ["POISON_EVENT_RETRY_LIMIT", "clear_checkpoint_after_gap_recovery"]

# Preserve the public lifecycle surface while keeping its crash protocol in a
# focused module below the repository's 500-line production-file gate.
ensure_subscription = event_lifecycle.ensure_subscription
recreate_subscription = event_lifecycle.recreate_subscription


def _now() -> datetime:
    return datetime.now(timezone.utc)


def poll_and_process_events(
    db: Session,
    org: Organization,
    *,
    client: BullhornService,
    provider_guard: Callable[[], None] | None = None,
) -> dict:
    """Drain + process the event queue honouring the destructive-read contract.

    Flow per batch:
      1. If a ``requestId`` checkpoint is already stored (crash mid-batch on a
         previous run), REPLAY that batch via ``refetch_events`` first — do not
         drain a new one until the replayed batch is processed + cleared.
      2. Otherwise drain a fresh batch via ``poll_events``; CHECKPOINT its
         ``requestId`` and COMMIT before processing any event.
      3. Process every event (idempotent re-fetch upserts); on completion clear
         the checkpoint and loop for the next batch, up to ``MAX_BATCHES_PER_CYCLE``.

    Returns counters. Never raises for a per-event failure. A batch with any
    failed event remains checkpointed and returns ``retry_pending`` so the next
    worker replays the exact same destructive read. A transport failure
    propagates so the task can retry without advancing its watermark.
    """
    sub_id = (org.bullhorn_event_subscription_id or "").strip()
    if not sub_id:
        return {"status": "no_subscription", "batches": 0, "events": 0}

    counters: dict[str, int] = {"batches": 0, "events": 0, "processed": 0, "errors": 0}
    guard = provider_guard or (lambda: None)
    try:
        event_lifecycle.validate_subscription_provenance(org)
    except event_lifecycle.SubscriptionProvenanceError:
        return {
            **counters,
            "status": "retry_pending",
            "reason": "invalid_subscription_provenance",
        }
    try:
        recovery = event_lifecycle.recover_poll_intent(
            db,
            org,
            client=client,
            provider_guard=provider_guard,
        )
    except InvalidEventBatch:
        return {
            **counters,
            "status": "retry_pending",
            "reason": "invalid_event_batch",
        }
    if recovery == "subscription_dead":
        return {**counters, "status": "subscription_dead"}
    if recovery == "subscription_reset":
        return {
            **counters,
            "status": "retry_pending",
            "reason": "subscription_reset",
        }
    if recovery == "request_sequence_gap":
        return {
            **counters,
            "status": "retry_pending",
            "reason": "request_sequence_gap",
        }
    if recovery == "superseded":
        return {**counters, "status": "retry_pending", "reason": "concurrent_poll"}
    for _ in range(MAX_BATCHES_PER_CYCLE):
        pending_request_id = (org.bullhorn_event_request_id or "").strip()
        if pending_request_id:
            checkpoint_epoch = event_lifecycle.event_epoch(org)
            # --- crash replay: re-fetch the last checkpointed batch ------------
            try:
                pending_request_id = event_lifecycle.normalize_request_id(
                    pending_request_id
                )
                guard()
                payload = client.refetch_events(
                    subscription_id=sub_id,
                    request_id=pending_request_id,
                    max_events=EVENT_BATCH_SIZE,
                )
                guard()
            except BullhornApiError as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code == 404:
                    # The subscription itself is gone/expired — surface so the
                    # caller recreates + runs a gap sweep (the checkpoint is dead).
                    return {**counters, "status": "subscription_dead"}
                # The requestId may be temporarily unavailable or superseded.
                # Never discard the only durable anchor here: a later 404 will
                # drive subscription recreation + a gap sweep, while transient
                # failures can replay normally on the next tick.
                logger.warning(
                    "Bullhorn event replay deferred org_id=%s batch=%s status=%s",
                    org.id,
                    _batch_fingerprint(pending_request_id),
                    status_code,
                )
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "replay_unavailable",
                }
            try:
                replay_events = event_lifecycle.validate_replay_payload(
                    payload,
                    expected_request_id=pending_request_id,
                )
            except InvalidEventBatch:
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "invalid_event_batch",
                }
            events = list(replay_events)
            if not events:
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "empty_replay",
                }
            batch_ok, failure_telemetry = _process_batch(
                db,
                org,
                events,
                client=client,
                counters=counters,
                provider_guard=provider_guard,
            )
            counters["batches"] += 1
            if not batch_ok:
                guard()
                poison = _record_poison_failure(
                    db,
                    org,
                    request_id=pending_request_id,
                    expected_event_epoch=checkpoint_epoch,
                    batch_size=len(events),
                    failure_telemetry=failure_telemetry,
                    provider_guard=provider_guard,
                )
                if poison is None:
                    return {
                        **counters,
                        "status": "retry_pending",
                        "reason": "concurrent_poll",
                    }
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "event_handler_failed",
                    "poison": poison,
                }
            guard()
            if not _clear_checkpoint(
                db,
                org,
                expected_request_id=pending_request_id,
                expected_event_epoch=checkpoint_epoch,
                provider_guard=provider_guard,
            ):
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "concurrent_poll",
                }
            # A replayed batch may be shorter than a full drain; keep looping to
            # drain anything that accumulated after the crash.
            continue

        # --- fresh destructive drain ------------------------------------------
        try:
            poll_epoch = event_lifecycle.prepare_fresh_poll(
                db,
                org,
                client=client,
                provider_guard=provider_guard,
            )
            guard()
            payload = client.poll_events(subscription_id=sub_id, max_events=EVENT_BATCH_SIZE)
            # The GET is destructive. If the lease disappeared while it was in
            # flight, leave the committed intent untouched for the next owner.
            guard()
        except event_lifecycle.EventPollSuperseded:
            return {**counters, "status": "retry_pending", "reason": "concurrent_poll"}
        except BullhornApiError as exc:
            if getattr(exc, "status_code", None) == 404:
                return {**counters, "status": "subscription_dead"}
            raise
        try:
            _validated_request_id, events = validate_event_batch(payload)
        except InvalidEventBatch:
            return {
                **counters,
                "status": "retry_pending",
                "reason": "invalid_event_batch",
            }
        try:
            guard()
            request_id = event_lifecycle.checkpoint_fresh_poll(
                db,
                org,
                payload=payload,
                has_events=bool(events),
                expected_intent_epoch=poll_epoch,
                provider_guard=provider_guard,
            )
        except event_lifecycle.SubscriptionAnchorReset:
            return {
                **counters,
                "status": "retry_pending",
                "reason": "subscription_reset",
            }
        except event_lifecycle.SubscriptionAnchorGap:
            return {
                **counters,
                "status": "retry_pending",
                "reason": "request_sequence_gap",
            }
        except event_lifecycle.EventPollSuperseded:
            return {**counters, "status": "retry_pending", "reason": "concurrent_poll"}
        if not events:
            break  # queue drained
        checkpoint_epoch = event_lifecycle.event_epoch(org)
        batch_ok, failure_telemetry = _process_batch(
            db,
            org,
            events,
            client=client,
            counters=counters,
            provider_guard=provider_guard,
        )
        counters["batches"] += 1
        if not batch_ok:
            if not request_id:
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "missing_request_id",
                    "handler_errors": int(failure_telemetry["error_count"]),
                }
            guard()
            poison = _record_poison_failure(
                db,
                org,
                request_id=request_id,
                expected_event_epoch=checkpoint_epoch,
                batch_size=len(events),
                failure_telemetry=failure_telemetry,
                provider_guard=provider_guard,
            )
            if poison is None:
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "concurrent_poll",
                }
            return {
                **counters,
                "status": "retry_pending",
                "reason": "event_handler_failed",
                "poison": poison,
            }
        guard()
        if not _clear_checkpoint(
            db,
            org,
            expected_request_id=request_id,
            expected_event_epoch=checkpoint_epoch,
            provider_guard=provider_guard,
        ):
            return {**counters, "status": "retry_pending", "reason": "concurrent_poll"}
        if len(events) < EVENT_BATCH_SIZE:
            break  # last (short) page — queue is drained

    counters["status"] = "ok"
    return counters


def _process_batch(
    db: Session,
    org: Organization,
    events: list[dict],
    *,
    client: BullhornService,
    counters: dict,
    provider_guard: Callable[[], None] | None = None,
) -> tuple[bool, dict]:
    """Dispatch each event through the entity handler; tally outcomes.

    Each ``dispatch_event`` isolates its own failure (the batch is already
    checkpointed), so one bad event never strands the rest. Re-fetch upserts are
    idempotent, so replaying a partially-processed batch after a crash is safe.
    """
    now = _now()
    errors_before = int(counters.get("errors") or 0)
    failed_entities: set[str] = set()
    failed_event_types: set[str] = set()
    guard = provider_guard or (lambda: None)
    for event in events:
        guard()
        counters["events"] += 1
        outcome = dispatch_event(
            db,
            org,
            event,
            client=client,
            now=now,
            provider_guard=provider_guard,
        )
        guard()
        if outcome == "error":
            counters["errors"] += 1
            entity = str(event.get("entityName") or "")
            failed_entities.add(
                entity if entity in SUBSCRIBED_ENTITIES else "unknown"
            )
            failed_event_types.add(normalize_event_type(event) or "unknown")
        elif outcome != "skipped":
            counters["processed"] += 1
    error_count = int(counters.get("errors") or 0) - errors_before
    return error_count == 0, {
        "error_count": error_count,
        "entity_types": sorted(failed_entities),
        "event_types": sorted(failed_event_types),
    }
