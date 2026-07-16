"""Celery task that drains the Graphiti episode outbox.

Realised-outcome (and decision) episodes are written to
``graph_episode_outbox`` in the producer's transaction rather than emitted
to Graphiti inline — so the irreplaceable signal survives a graph outage.
This task ships the pending rows to Graphiti with retry/backoff.

Two layers of retry, deliberately:
- *Per row* (inside ``episode_outbox.drain``): a send that doesn't land
  leaves the row ``pending`` with bounded exponential cooldown, so a future
  beat tick retries it. Provider, budget, and metering outages never exhaust
  into terminal failure; only an invalid payload does.
- *Per task* (``self.retry`` below): only for an unexpected failure in the
  drain machinery itself (e.g. DB blip opening the session). Bounded
  backoff; on exhaustion the beat schedule re-runs it anyway.

Scheduled by ``celery_app`` (see beat_schedule). Manual trigger:
``celery -A app.tasks.celery_app call
app.tasks.graph_outbox_tasks.drain_graph_episode_outbox``.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.tasks.graph_outbox")


_MAX_RETRIES = 3
_BACKOFF_CAP_SECONDS = 600
_DRAIN_ERROR = "graph_outbox_drain_failed"


def _retry_countdown(retries: int) -> int:
    """Bounded exponential backoff: 60s → 120s → 240s … capped at 10min."""
    return min(_BACKOFF_CAP_SECONDS, 60 * (2 ** max(0, retries)))


@celery_app.task(
    bind=True,
    name="app.tasks.graph_outbox_tasks.drain_graph_episode_outbox",
    max_retries=_MAX_RETRIES,
)
def drain_graph_episode_outbox(self, batch_size: int = 200) -> dict:
    """Send pending ``graph_episode_outbox`` rows to Graphiti.

    Idempotent: rows already ``sent`` are excluded and pending rows are locked
    with ``SKIP LOCKED``, so competing drains do not double-process (Graphiti
    content dedup is a second backstop). No-op when Graphiti is unconfigured —
    rows are left untouched for a future drain.
    """
    from ..candidate_graph import episode_outbox

    db = SessionLocal()
    try:
        summary = episode_outbox.drain(db, batch_size=int(batch_size))
        logger.info("graph_episode_outbox drain: %s", summary)
        return summary
    except Exception:  # unexpected machinery failure — bounded retry
        db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=_retry_countdown(self.request.retries))
        logger.exception("graph_episode_outbox drain failed (retries exhausted)")
        return {"status": "error", "error": _DRAIN_ERROR}
    finally:
        db.close()


__all__ = ["drain_graph_episode_outbox"]
