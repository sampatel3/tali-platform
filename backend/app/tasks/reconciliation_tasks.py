"""Celery tasks for Anthropic usage reconciliation.

Scheduled by ``celery_app`` to run once a day. Pulls Anthropic Admin
API usage + cost reports for the trailing 48h window and writes / upserts
``anthropic_usage_reconciliations`` rows. UI surfaces drift > 1%.

Manual trigger: ``celery -A app.tasks.celery_app call
app.tasks.reconciliation_tasks.reconcile_anthropic_usage`` — useful for
backfilling after the migration runs.
"""
from __future__ import annotations

import logging

from .celery_app import celery_app
from ..platform.database import SessionLocal
from ..services.anthropic_reconciliation_service import reconcile_recent

logger = logging.getLogger("taali.tasks.reconciliation")


@celery_app.task(name="app.tasks.reconciliation_tasks.reconcile_anthropic_usage")
def reconcile_anthropic_usage(days: int = 2) -> dict:
    """Reconcile the last ``days`` days of Anthropic billing.

    The default 2-day window catches late-arriving Anthropic data on
    day-1 while still reconciling day-2's data with the freshest
    available numbers. Idempotent: the service upserts rows.
    """
    db = SessionLocal()
    try:
        summary = reconcile_recent(db, days=int(days))
        logger.info(
            "anthropic_reconciliation: days=%d summary=%s",
            int(days),
            summary,
        )
        return summary
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("anthropic_reconciliation failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()
