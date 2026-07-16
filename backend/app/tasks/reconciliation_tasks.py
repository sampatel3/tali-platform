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
from ..services.anthropic_reconciliation_service import (
    _RECONCILE_LOOKBACK_DAYS,
    reconcile_recent,
)

logger = logging.getLogger("taali.tasks.reconciliation")
_RECONCILIATION_ERROR = "anthropic_reconciliation_failed"


@celery_app.task(name="app.tasks.reconciliation_tasks.reconcile_anthropic_usage")
def reconcile_anthropic_usage(days: int = _RECONCILE_LOOKBACK_DAYS) -> dict:
    """Reconcile the last ``days`` days of Anthropic billing.

    The default window re-reconciles each recent day until OUR late-arriving
    internal rows (batch retrievals land hours-to-days after billing) have
    settled — not because Anthropic's data is late (it settles in ~5 min).
    The weekly settle sweep passes a wider ``days`` for stragglers.
    Idempotent: the service upserts rows by (date, workspace, model).
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
        logger.exception(
            "anthropic_reconciliation failed error_type=%s",
            type(exc).__name__,
        )
        return {"error": _RECONCILIATION_ERROR}
    finally:
        db.close()
