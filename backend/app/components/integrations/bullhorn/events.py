"""Bullhorn incremental event polling — the destructive-read contract.

Bullhorn's event subscription is a DURABLE, DESTRUCTIVE queue. A normal poll
drains up to ``maxEvents`` and stamps the batch with a ``requestId``; the events
are gone from the queue the instant they're returned. Re-issuing the SAME
``requestId`` re-fetches ONLY that last drained batch (crash-replay) without
draining more. Subscriptions and their unread events expire after ~30 days.

Crash-safety invariant (build plan §6): we checkpoint the batch's ``requestId``
onto ``org.bullhorn_event_request_id`` and COMMIT it BEFORE processing any event.
If the worker dies mid-batch, the next run sees a stored ``requestId`` and calls
``refetch_events(request_id)`` to replay exactly that batch instead of losing it.
Only once a batch is fully processed do we clear the checkpoint and drain the
next batch. Events are dirty-flags, so processing = re-fetch the entity via the
full-sync upsert helpers (see :mod:`event_handlers`).

Subscription lifecycle:
* :func:`ensure_subscription` creates the subscription if the org has none, or if
  the stored one is gone/expired (a poll 404s). On a (re)create it signals that a
  GAP-COVERING full sweep is needed — the queue starts empty at creation, so any
  change during the outage window is invisible to events and must be swept via
  the full/``dateLastModified`` path. The caller runs that sweep.

Gating: every entry point no-ops when ``BULLHORN_ENABLED`` is off or the org
isn't connected — enforced by the task layer before we're called, and re-checked
here defensively.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ....models.organization import Organization
from .errors import BullhornApiError
from .event_handlers import SUBSCRIBED_ENTITIES, dispatch_event
from .service import BullhornService

logger = logging.getLogger("taali.bullhorn.events")

# How many events to drain per poll. Bullhorn caps a poll response; a modest
# batch keeps each transaction bounded and the requestId-replay window small.
EVENT_BATCH_SIZE = 100
# Absolute cap on batches drained per poll cycle, so one very busy org can't
# monopolise a beat tick (rate budget matters — 100k calls/mo default). The next
# cycle picks up where this left off.
MAX_BATCHES_PER_CYCLE = 20
# Retry a failed, exactly-replayable batch a small number of times before the
# incremental runner replaces it through a clean gap sweep + reconciliation.
POISON_EVENT_RETRY_LIMIT = 3
_POISON_STATE_KEY = "event_poison_checkpoint"
_SAFE_EVENT_TYPES = {"INSERTED", "UPDATED", "DELETED", "DELETE"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _subscription_id(org: Organization) -> str:
    """Stable per-org subscription id. Reused across recreates for one org."""
    stored = (org.bullhorn_event_subscription_id or "").strip()
    if stored:
        return stored
    return f"taali-{org.id}-{uuid.uuid4().hex[:8]}"


def ensure_subscription(
    db: Session, org: Organization, *, client: BullhornService
) -> tuple[str, bool]:
    """Ensure the org has a subscription id. Returns ``(sub_id, created)``.

    Create-ONLY-when-absent: if the org already has a stored subscription id we
    trust it and return ``created=False`` WITHOUT probing — a liveness probe here
    would have to be a destructive poll (Bullhorn has no non-destructive status
    read), which would clobber a pending crash-replay batch. Instead, a
    dead/expired subscription is detected lazily by :func:`poll_and_process_events`
    (its poll 404s → ``status="subscription_dead"``), and the caller then calls
    :func:`recreate_subscription` + runs the gap sweep. ``created=True`` (first
    ever subscription) also warrants a gap sweep since the queue starts empty.
    """
    stored = (org.bullhorn_event_subscription_id or "").strip()
    if stored:
        return stored, False
    sub_id = _subscription_id(org)
    client.create_subscription(subscription_id=sub_id, entity_names=list(SUBSCRIBED_ENTITIES))
    org.bullhorn_event_subscription_id = sub_id
    org.bullhorn_event_request_id = None  # fresh queue → no replay checkpoint
    _drop_poison_state(org)
    db.add(org)
    db.commit()
    logger.info("Bullhorn subscription created org_id=%s sub_id=%s", org.id, sub_id)
    return sub_id, True


def recreate_subscription(db: Session, org: Organization, *, client: BullhornService) -> str:
    """Replace a dead/expired subscription with a fresh one. Returns the sub id.

    Called after :func:`poll_and_process_events` reports ``subscription_dead``
    (a 30-day expiry, or the subscription vanished). We reuse the same stable id
    (PUT replaces), start a fresh empty queue, and DROP any replay checkpoint —
    the checkpointed batch belonged to the dead subscription and is unrecoverable.
    The caller MUST then run a gap-covering sweep to backfill the outage window.
    """
    sub_id = _subscription_id(org)
    try:
        client.delete_subscription(subscription_id=sub_id)
    except Exception:  # pragma: no cover — best-effort; the PUT below replaces anyway
        logger.info("Bullhorn subscription delete-before-recreate no-op org_id=%s", org.id)
    client.create_subscription(subscription_id=sub_id, entity_names=list(SUBSCRIBED_ENTITIES))
    org.bullhorn_event_subscription_id = sub_id
    org.bullhorn_event_request_id = None
    _drop_poison_state(org)
    db.add(org)
    db.commit()
    logger.info("Bullhorn subscription recreated org_id=%s sub_id=%s", org.id, sub_id)
    return sub_id


def poll_and_process_events(
    db: Session, org: Organization, *, client: BullhornService
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
    for _ in range(MAX_BATCHES_PER_CYCLE):
        pending_request_id = (org.bullhorn_event_request_id or "").strip()
        if pending_request_id:
            # --- crash replay: re-fetch the last checkpointed batch ------------
            try:
                payload = client.refetch_events(
                    subscription_id=sub_id, request_id=pending_request_id, max_events=EVENT_BATCH_SIZE
                )
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
            events = _events_of(payload)
            if not events:
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "empty_replay",
                }
            batch_ok, failure_telemetry = _process_batch(
                db, org, events, client=client, counters=counters
            )
            counters["batches"] += 1
            if not batch_ok:
                poison = _record_poison_failure(
                    db,
                    org,
                    request_id=pending_request_id,
                    batch_size=len(events),
                    failure_telemetry=failure_telemetry,
                )
                return {
                    **counters,
                    "status": "retry_pending",
                    "reason": "event_handler_failed",
                    "poison": poison,
                }
            _clear_checkpoint(db, org)
            # A replayed batch may be shorter than a full drain; keep looping to
            # drain anything that accumulated after the crash.
            continue

        # --- fresh destructive drain ------------------------------------------
        try:
            payload = client.poll_events(subscription_id=sub_id, max_events=EVENT_BATCH_SIZE)
        except BullhornApiError as exc:
            if getattr(exc, "status_code", None) == 404:
                return {**counters, "status": "subscription_dead"}
            raise
        events = _events_of(payload)
        request_id = str(payload.get("requestId") or "").strip() if isinstance(payload, dict) else ""
        if not events:
            break  # queue drained
        # CHECKPOINT BEFORE PROCESSING: commit the requestId so a crash mid-batch
        # replays exactly these events instead of losing them.
        if request_id:
            org.bullhorn_event_request_id = request_id
            db.add(org)
            db.commit()
        else:
            # No requestId on a non-empty batch: we cannot checkpoint, so a crash
            # mid-batch would replay via the gap sweep rather than exact replay.
            # Process anyway (losing events is worse) but flag the missing anchor.
            logger.warning(
                "Bullhorn event batch has no requestId org_id=%s events=%d — "
                "processing without a replay checkpoint",
                org.id,
                len(events),
            )
        batch_ok, failure_telemetry = _process_batch(
            db, org, events, client=client, counters=counters
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
            poison = _record_poison_failure(
                db,
                org,
                request_id=request_id,
                batch_size=len(events),
                failure_telemetry=failure_telemetry,
            )
            return {
                **counters,
                "status": "retry_pending",
                "reason": "event_handler_failed",
                "poison": poison,
            }
        if not request_id:
            # The destructive batch succeeded, but without an exact replay
            # anchor we cannot prove crash safety. Keep the prior watermark and
            # let the caller run a gap sweep from it.
            return {
                **counters,
                "status": "retry_pending",
                "reason": "missing_request_id",
            }
        _clear_checkpoint(db, org)
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
    for event in events:
        counters["events"] += 1
        outcome = dispatch_event(db, org, event, client=client, now=now)
        if outcome == "error":
            counters["errors"] += 1
            entity = str(event.get("entityName") or "")
            failed_entities.add(
                entity if entity in SUBSCRIBED_ENTITIES else "unknown"
            )
            event_type = str(event.get("eventType") or "").upper()
            failed_event_types.add(
                event_type if event_type in _SAFE_EVENT_TYPES else "unknown"
            )
        elif outcome != "skipped":
            counters["processed"] += 1
    error_count = int(counters.get("errors") or 0) - errors_before
    return error_count == 0, {
        "error_count": error_count,
        "entity_types": sorted(failed_entities),
        "event_types": sorted(failed_event_types),
    }


def _events_of(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def _clear_checkpoint(db: Session, org: Organization) -> None:
    if (org.bullhorn_event_request_id or "") == "":
        return
    poison = _poison_state(org)
    org.bullhorn_event_request_id = None
    _drop_poison_state(org)
    if poison is not None:
        _record_public_poison(
            org,
            {
                **_public_poison(poison),
                "status": "replayed_successfully",
                "resolved_at": _now().isoformat(),
            },
        )
    db.add(org)
    db.commit()


def clear_checkpoint_after_gap_recovery(
    db: Session,
    org: Organization,
    *,
    expected_request_id: str,
    reconciliation: dict | None = None,
) -> bool:
    """Clear an unreplayable anchor only after its gap sweep succeeded.

    The expected-id guard is defensive (the per-org mutex already serializes
    this path): a recovery for one batch must never clear a newer checkpoint.
    """
    current = str(org.bullhorn_event_request_id or "").strip()
    if not expected_request_id or current != expected_request_id:
        return False
    poison = _poison_state(org)
    org.bullhorn_event_request_id = None
    _drop_poison_state(org)
    if poison is not None and str(poison.get("request_id") or "") == current:
        discrepancies = (
            reconciliation.get("discrepancies")
            if isinstance(reconciliation, dict)
            and isinstance(reconciliation.get("discrepancies"), dict)
            else {}
        )
        _record_public_poison(
            org,
            {
                **_public_poison(poison),
                "status": "recovered_by_gap_sweep",
                "resolved_at": _now().isoformat(),
                "reconciliation_ok": bool(
                    isinstance(reconciliation, dict)
                    and reconciliation.get("ok") is True
                ),
                "discrepancy_entities": sorted(discrepancies),
            },
        )
    db.add(org)
    db.commit()
    return True


def _poison_state(org: Organization) -> dict | None:
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    state = config.get(_POISON_STATE_KEY)
    return dict(state) if isinstance(state, dict) else None


def _drop_poison_state(org: Organization) -> None:
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    if _POISON_STATE_KEY in config:
        config.pop(_POISON_STATE_KEY, None)
        org.bullhorn_config = config


def _public_poison(state: dict) -> dict:
    return {
        "code": "event_handler_failed",
        "attempts": int(state.get("attempts") or 0),
        "retry_limit": POISON_EVENT_RETRY_LIMIT,
        "batch_fingerprint": str(state.get("batch_fingerprint") or ""),
        "batch_size": int(state.get("batch_size") or 0),
        "error_count": int(state.get("error_count") or 0),
        "entity_types": list(state.get("entity_types") or []),
        "event_types": list(state.get("event_types") or []),
        "first_failed_at": state.get("first_failed_at"),
        "last_failed_at": state.get("last_failed_at"),
    }


def _record_public_poison(org: Organization, telemetry: dict) -> None:
    summary = (
        dict(org.bullhorn_last_sync_summary)
        if isinstance(org.bullhorn_last_sync_summary, dict)
        else {}
    )
    summary["event_poison_batch"] = telemetry
    org.bullhorn_last_sync_summary = summary


def _record_poison_failure(
    db: Session,
    org: Organization,
    *,
    request_id: str,
    batch_size: int,
    failure_telemetry: dict,
) -> dict:
    """Durably count one identical failed replay without storing event payloads."""
    now = _now().isoformat()
    prior = _poison_state(org)
    signature = _failure_signature(failure_telemetry)
    same_failure = bool(
        prior is not None
        and str(prior.get("request_id") or "") == str(request_id)
        and str(prior.get("failure_signature") or "") == signature
    )
    attempts = min(
        POISON_EVENT_RETRY_LIMIT,
        (int(prior.get("attempts") or 0) if same_failure and prior else 0) + 1,
    )
    state = {
        "request_id": str(request_id),
        "attempts": attempts,
        "batch_fingerprint": _batch_fingerprint(request_id),
        "failure_signature": signature,
        "batch_size": int(batch_size),
        "error_count": int(failure_telemetry.get("error_count") or 0),
        "entity_types": list(failure_telemetry.get("entity_types") or []),
        "event_types": list(failure_telemetry.get("event_types") or []),
        "first_failed_at": (
            prior.get("first_failed_at") if same_failure and prior else now
        ),
        "last_failed_at": now,
    }
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    config[_POISON_STATE_KEY] = state
    org.bullhorn_config = config
    public = {
        **_public_poison(state),
        "status": (
            "recovery_due"
            if attempts >= POISON_EVENT_RETRY_LIMIT
            else "retrying_exact_batch"
        ),
    }
    _record_public_poison(org, public)
    db.add(org)
    db.commit()
    return public


def _batch_fingerprint(request_id: str) -> str:
    return hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()[:12]


def _failure_signature(telemetry: dict) -> str:
    source = "|".join(
        (
            str(int(telemetry.get("error_count") or 0)),
            ",".join(telemetry.get("entity_types") or []),
            ",".join(telemetry.get("event_types") or []),
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
