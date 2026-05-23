"""Celery tasks for the model-refinement loop.

These run nightly to keep the cv_match calibrator fed:

1. ``score_terminal_for_calibration`` — score `advanced` (terminal / decided)
   candidates that lack a Tali score, so each realized outcome (offer/hired =
   positive, reject = negative) has a paired prediction. Pure scoring only:
   metered, no agent decisions, emails, or Workable changes. Already-scored
   candidates are skipped, so it never re-scores.

The recalibration task that *consumes* these pairs is added separately.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.calibration")

# Bound the per-run work so a one-time backlog can't blow the nightly Anthropic
# budget in a single sweep — it drains over a few nights instead.
_NIGHTLY_SCORE_LIMIT = 150


@celery_app.task(name="app.tasks.calibration_tasks.score_terminal_for_calibration")
def score_terminal_for_calibration(limit: int = _NIGHTLY_SCORE_LIMIT) -> dict:
    """Score unscored `advanced` candidates (training-data prep). Returns the
    run summary dict from ``score_advanced_for_training``."""
    from ..scripts.score_advanced_for_training import score_advanced_for_training
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        summary = score_advanced_for_training(db, apply=True, limit=limit)
    except Exception:
        db.rollback()
        logger.exception("score_terminal_for_calibration failed")
        raise
    finally:
        db.close()
    logger.info("score_terminal_for_calibration: %s", summary)
    return summary
