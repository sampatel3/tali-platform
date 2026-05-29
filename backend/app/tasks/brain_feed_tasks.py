"""Celery task that sweeps + drains the outbound mainspring brain feed.

One beat task does both halves of the outbound hop:
  1. ``sweep_and_enqueue`` — enqueue newly-resolved decisions, teach outcomes,
     and whole-day usage rollups (anonymized) into ``brain_feed_outbox``.
  2. ``drain`` — POST pending rows to mainspring's ingest API.

No-op when ``MAINSPRING_BRAIN_FEED_ENABLED`` is off (the default), so the live
platform is unaffected until the feed is deliberately turned on. With the flag
on but no ingest URL configured, the drain runs in shadow (log-only).

Scheduled by ``celery_app`` (see beat_schedule). Manual trigger:
``celery -A app.tasks.celery_app call
app.tasks.brain_feed_tasks.flush_brain_feed``.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.tasks.brain_feed")


_MAX_RETRIES = 3
_BACKOFF_CAP_SECONDS = 600


def _retry_countdown(retries: int) -> int:
    """Bounded exponential backoff: 60s → 120s → 240s … capped at 10min."""
    return min(_BACKOFF_CAP_SECONDS, 60 * (2 ** max(0, retries)))


@celery_app.task(
    bind=True,
    name="app.tasks.brain_feed_tasks.flush_brain_feed",
    max_retries=_MAX_RETRIES,
)
def flush_brain_feed(self) -> dict:
    """Sweep new records into the outbox, then drain pending rows to mainspring.

    Idempotent end-to-end: the sweep skips already-enqueued source rows
    (``event_id``) and the drain skips already-``sent`` rows, so re-running
    never double-processes.
    """
    from ..brain_feed import outbox, sweep

    db = SessionLocal()
    try:
        swept = sweep.sweep_and_enqueue(db)
        drained = outbox.drain(db)
        summary = {"swept": swept, "drained": drained}
        if swept.get("status") != "disabled":
            logger.info("brain_feed flush: %s", summary)
        return summary
    except Exception as exc:  # unexpected machinery failure — bounded retry
        db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=_retry_countdown(self.request.retries))
        logger.exception("brain_feed flush failed (retries exhausted)")
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


__all__ = ["flush_brain_feed"]
