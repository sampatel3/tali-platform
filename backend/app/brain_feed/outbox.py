"""Durable outbox for the outbound mainspring brain feed.

``enqueue`` writes a pending ``brain_feed_outbox`` row, idempotent on
``event_id`` (a re-sweep of the same source row is a no-op). ``drain`` ships
pending rows to mainspring's ingest API and marks them ``sent``; a send that
doesn't land leaves the row ``pending`` (until a retry cap) so signal is never
silently dropped.

Posture is governed entirely by config (see ``app.platform.config``):
  - flag off (default)       -> ``enqueue`` is a no-op; nothing is written.
  - flag on, no ingest URL   -> ``drain`` runs in shadow (log-only, rows stay
                                 pending for when the endpoint comes online).
  - flag on, ingest URL set  -> ``drain`` POSTs to /api/v1/ingest/<plural>.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.brain_feed_outbox import (
    BRAIN_FEED_KINDS,
    BRAIN_FEED_STATUS_FAILED,
    BRAIN_FEED_STATUS_PENDING,
    BRAIN_FEED_STATUS_PROCESSING,
    BRAIN_FEED_STATUS_SENT,
    BrainFeedOutbox,
)
from ..platform.config import settings


logger = logging.getLogger("taali.brain_feed.outbox")

# Bounded retry budget per row before it's parked as ``failed`` (mirrors the
# graph episode outbox). The drain runs on a beat schedule, so ~8 attempts
# comfortably outlast a transient mainspring outage.
_MAX_ATTEMPTS = 8
_DRAIN_BATCH_SIZE = 200
_POST_TIMEOUT_SECONDS = 10.0
_LEASE_SECONDS = 120
_DELIVERY_ERROR = "brain_feed_delivery_failed"

# record_kind (singular) -> mainspring ingest path segment (plural).
_INGEST_PATH = {
    "decision": "decisions",
    "outcome": "outcomes",
    "usage": "usage",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    *,
    record_kind: str,
    event_id: str,
    payload: dict[str, Any],
) -> Optional[BrainFeedOutbox]:
    """Insert one pending outbox row. Idempotent on ``event_id``.

    Returns the *newly-created* row, or None when nothing new was written —
    either because the feature is disabled or because a row with this
    ``event_id`` already exists. (So a caller can treat a non-None return as
    "this was a fresh enqueue".) Disabled is a no-op so the sweep can call it
    unconditionally and the live platform stays unaffected by default.
    """
    if not settings.MAINSPRING_BRAIN_FEED_ENABLED:
        return None
    if record_kind not in BRAIN_FEED_KINDS:
        raise ValueError(f"unknown brain-feed record_kind: {record_kind!r}")
    existing = (
        db.query(BrainFeedOutbox)
        .filter(BrainFeedOutbox.event_id == event_id)
        .one_or_none()
    )
    if existing is not None:
        return None
    row = BrainFeedOutbox(
        record_kind=record_kind,
        event_id=event_id,
        payload=payload,
        status=BRAIN_FEED_STATUS_PENDING,
        attempts=0,
    )
    db.add(row)
    db.flush()
    return row


def _post(row: BrainFeedOutbox, base_url: str, token: str) -> None:
    """POST one row to mainspring. Raises on any non-2xx / transport error."""
    path = _INGEST_PATH[row.record_kind]
    url = f"{base_url.rstrip('/')}/api/v1/ingest/{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"event_id": row.event_id, "payload": row.payload}
    resp = httpx.post(url, json=body, headers=headers, timeout=_POST_TIMEOUT_SECONDS)
    resp.raise_for_status()


def _retry_delay(attempts: int, row_id: int) -> int:
    base = min(1800, 30 * (2 ** max(0, attempts - 1)))
    return base + ((int(row_id) * 37 + attempts * 17) % 16)


def _eligible(now: datetime):
    return or_(
        and_(
            BrainFeedOutbox.status == BRAIN_FEED_STATUS_PENDING,
            or_(
                BrainFeedOutbox.next_attempt_at.is_(None),
                BrainFeedOutbox.next_attempt_at <= now,
            ),
        ),
        and_(
            BrainFeedOutbox.status == BRAIN_FEED_STATUS_PROCESSING,
            or_(
                BrainFeedOutbox.lease_until.is_(None),
                BrainFeedOutbox.lease_until <= now,
            ),
        ),
    )


def _claim(db: Session, *, batch_size: int) -> list[BrainFeedOutbox]:
    """Lease a disjoint batch; Postgres SKIP LOCKED supports many drainers."""
    now = _now()
    rows = (
        db.query(BrainFeedOutbox)
        .filter(_eligible(now))
        .order_by(BrainFeedOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(batch_size)))
        .all()
    )
    # The lease covers the bounded sequential network budget for the whole
    # claimed sub-batch; later rows must not become claimable while this worker
    # is still legitimately working through earlier 10-second calls.
    lease_seconds = max(
        _LEASE_SECONDS,
        int(len(rows) * (_POST_TIMEOUT_SECONDS + 2) + 30),
    )
    lease_until = now + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = BRAIN_FEED_STATUS_PROCESSING
        # Count the durable claim, not merely handled exceptions, so repeated
        # worker deaths also consume the bounded retry budget.
        row.attempts = int(row.attempts or 0) + 1
        row.lease_until = lease_until
        row.next_attempt_at = None
    db.commit()
    return rows


def drain(
    db: Session,
    *,
    batch_size: int = _DRAIN_BATCH_SIZE,
    max_attempts: int = _MAX_ATTEMPTS,
) -> dict:
    """Ship pending outbox rows to mainspring. Idempotent + retry-safe.

    Returns a summary dict. ``status`` is one of:
      - ``disabled``    : feature flag off (no rows touched).
      - ``shadow``      : flag on but no ingest URL — counted + logged, not sent.
      - ``ok``          : at least one send attempt was made.
    """
    if not settings.MAINSPRING_BRAIN_FEED_ENABLED:
        return {"status": "disabled", "scanned": 0, "sent": 0, "failed": 0}

    base_url = (settings.MAINSPRING_INGEST_URL or "").strip()
    if not base_url:
        # Shadow: the feed is enabled but the endpoint isn't live yet. Leave
        # rows pending so they ship once a URL is configured; just report.
        pending = db.query(BrainFeedOutbox).filter(_eligible(_now())).count()
        logger.info(
            "brain_feed drain (shadow, no ingest URL): %d pending row(s) would be sent",
            pending,
        )
        return {"status": "shadow", "scanned": pending, "sent": 0, "failed": 0}

    rows = _claim(db, batch_size=batch_size)
    token = (settings.MAINSPRING_BRAND_TOKEN or "").strip()
    sent = 0
    failed = 0
    still_pending = 0
    for row in rows:
        now = _now()
        try:
            _post(row, base_url, token)
            row.status = BRAIN_FEED_STATUS_SENT
            row.sent_at = now
            row.updated_at = now
            row.lease_until = None
            row.next_attempt_at = None
            sent += 1
        except Exception as exc:
            logger.exception(
                "brain_feed delivery failed row_id=%s error_type=%s",
                row.id,
                type(exc).__name__,
            )
            row.last_error = _DELIVERY_ERROR
            row.updated_at = now
            row.lease_until = None
            if row.attempts >= int(max_attempts):
                row.status = BRAIN_FEED_STATUS_FAILED
                row.next_attempt_at = None
                failed += 1
            else:
                row.status = BRAIN_FEED_STATUS_PENDING
                row.next_attempt_at = now + timedelta(
                    seconds=_retry_delay(int(row.attempts), int(row.id))
                )
                still_pending += 1
        # Acknowledge each external side effect independently. A later slow or
        # failing row cannot cause an already-sent row to be replayed.
        db.commit()
    if failed:
        logger.warning(
            "brain_feed drain: scanned=%d sent=%d failed=%d pending=%d",
            len(rows), sent, failed, still_pending,
        )
    return {
        "status": "ok",
        "scanned": len(rows),
        "sent": sent,
        "failed": failed,
        "pending": still_pending,
    }


__all__ = ["enqueue", "drain"]
