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
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from ..models.brain_feed_outbox import (
    BRAIN_FEED_KINDS,
    BRAIN_FEED_STATUS_FAILED,
    BRAIN_FEED_STATUS_PENDING,
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

    rows = (
        db.query(BrainFeedOutbox)
        .filter(BrainFeedOutbox.status == BRAIN_FEED_STATUS_PENDING)
        .order_by(BrainFeedOutbox.id.asc())
        .limit(int(batch_size))
        .all()
    )

    base_url = (settings.MAINSPRING_INGEST_URL or "").strip()
    if not base_url:
        # Shadow: the feed is enabled but the endpoint isn't live yet. Leave
        # rows pending so they ship once a URL is configured; just report.
        logger.info(
            "brain_feed drain (shadow, no ingest URL): %d pending row(s) would be sent",
            len(rows),
        )
        return {"status": "shadow", "scanned": len(rows), "sent": 0, "failed": 0}

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
            sent += 1
        except Exception as exc:
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = str(exc)[:1000]
            row.updated_at = now
            if row.attempts >= int(max_attempts):
                row.status = BRAIN_FEED_STATUS_FAILED
                failed += 1
            else:
                still_pending += 1

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
