"""Celery task: sweep scored provider assessments + drain result callbacks to Workable.

One beat task does both halves of the outbound hop:
  1. ``enqueue_completed_results`` — enqueue a 'completed' callback per newly
     scored provider assessment (idempotent on ``workable_provider_pushed_at``).
  2. ``drain`` — PUT pending rows to each assessment's Workable ``callback_url``.

No-op when ``WORKABLE_PROVIDER_ENABLED`` is off (the default), so the live
platform is unaffected until the marketplace add-on is deliberately enabled.
Manual trigger: ``celery -A app.tasks.celery_app call
app.tasks.workable_provider_tasks.flush_workable_provider``.
"""
from __future__ import annotations

import logging

from .celery_app import celery_app
from ..platform.config import settings
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.tasks.workable_provider")

_MAX_RETRIES = 3
_BACKOFF_CAP_SECONDS = 600
_FLUSH_ERROR = "workable_provider_flush_failed"


def _retry_countdown(retries: int) -> int:
    """Bounded exponential backoff: 60s → 120s → 240s … capped at 10min."""
    return min(_BACKOFF_CAP_SECONDS, 60 * (2 ** max(0, retries)))


@celery_app.task(
    bind=True,
    name="app.tasks.workable_provider_tasks.flush_workable_provider",
    max_retries=_MAX_RETRIES,
)
def flush_workable_provider(self) -> dict:
    """Sweep newly-scored provider assessments into the outbox, then drain
    pending result callbacks to Workable. Idempotent end-to-end."""
    if not settings.WORKABLE_PROVIDER_ENABLED:
        return {"status": "disabled"}

    from ..domains.workable_provider import outbox, service

    db = SessionLocal()
    try:
        swept = service.enqueue_completed_results(db)
        drained = outbox.drain(db)
        summary = {"swept": swept, "drained": drained}
        logger.info("workable_provider flush: %s", summary)
        return summary
    except Exception:  # unexpected machinery failure — bounded retry
        db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=_retry_countdown(self.request.retries))
        logger.exception("workable_provider flush failed (retries exhausted)")
        return {"status": "error", "error": _FLUSH_ERROR}
    finally:
        db.close()


__all__ = ["flush_workable_provider"]
