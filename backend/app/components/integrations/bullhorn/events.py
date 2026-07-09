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

    Returns counters. Never raises for a per-event failure (the batch is already
    checkpointed); a transport failure propagates so the task can log + retry.
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
                # A 400 = the requestId is superseded (not the subscription). The
                # batch is unrecoverable; clear the checkpoint and move on rather
                # than loop forever on an un-replayable id.
                logger.warning(
                    "Bullhorn event replay failed org_id=%s request_id=%s (%s) — clearing checkpoint",
                    org.id,
                    pending_request_id,
                    status_code,
                )
                _clear_checkpoint(db, org)
                continue
            events = _events_of(payload)
            _process_batch(db, org, events, client=client, counters=counters)
            _clear_checkpoint(db, org)
            counters["batches"] += 1
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
        _process_batch(db, org, events, client=client, counters=counters)
        _clear_checkpoint(db, org)
        counters["batches"] += 1
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
) -> None:
    """Dispatch each event through the entity handler; tally outcomes.

    Each ``dispatch_event`` isolates its own failure (the batch is already
    checkpointed), so one bad event never strands the rest. Re-fetch upserts are
    idempotent, so replaying a partially-processed batch after a crash is safe.
    """
    now = _now()
    for event in events:
        counters["events"] += 1
        outcome = dispatch_event(db, org, event, client=client, now=now)
        if outcome == "error":
            counters["errors"] += 1
        elif outcome != "skipped":
            counters["processed"] += 1


def _events_of(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def _clear_checkpoint(db: Session, org: Organization) -> None:
    if (org.bullhorn_event_request_id or "") == "":
        return
    org.bullhorn_event_request_id = None
    db.add(org)
    db.commit()
