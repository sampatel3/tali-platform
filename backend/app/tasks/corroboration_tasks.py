"""Async cross-source corroboration enrichment (shortlist-gated).

Runs the slow corroboration axes (graph + GitHub fetch) off the scoring
hot path, only for shortlist candidates. Dispatched post-score-commit by
``scoring_tasks.score_application_job``; the gate is re-checked here so a stale
enqueue is a cheap no-op.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app

logger = logging.getLogger("taali.corroboration_tasks")


@celery_app.task(
    name="app.tasks.corroboration_tasks.enrich_corroboration_job",
    bind=True,
    max_retries=2,
    queue="scoring",
    acks_late=True,
    reject_on_worker_lost=True,
)
def enrich_corroboration_job(
    self,
    application_id: int,
) -> dict:
    from ..platform.database import SessionLocal
    from ..services.corroboration_enrichment import run_corroboration_enrichment

    db = SessionLocal()
    try:
        result = run_corroboration_enrichment(
            db,
            application_id=int(application_id),
        )
        if result.get("status") in {"leased", "retry_wait"}:
            retry_after = max(
                1,
                min(1800, int(result.get("retry_after_seconds") or 60)),
            )
            if int(self.request.retries or 0) < int(self.max_retries or 0):
                raise self.retry(
                    countdown=retry_after,
                    args=[int(application_id)],
                )
        return result
    finally:
        db.close()
