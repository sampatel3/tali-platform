"""Async cross-source corroboration enrichment (shortlist-gated).

Runs the paid/slow corroboration axes (graph + LinkedIn fetch) off the scoring
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
    max_retries=0,
    queue="scoring",
)
def enrich_corroboration_job(application_id: int) -> dict:
    from ..models.candidate_application import CandidateApplication
    from ..platform.database import SessionLocal
    from ..services.corroboration_enrichment import enrich_corroboration, should_enrich

    db = SessionLocal()
    try:
        application = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .first()
        )
        if application is None:
            return {"status": "missing", "application_id": application_id}
        if not should_enrich(application):
            return {"status": "skipped", "application_id": application_id}
        triangulation = enrich_corroboration(application, db)
        return {
            "status": "ok" if triangulation else "no_signal",
            "application_id": application_id,
            "verdict": (triangulation or {}).get("verdict"),
        }
    finally:
        db.close()
