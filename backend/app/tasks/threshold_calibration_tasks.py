"""Celery task for the role_fit threshold calibration.

One nightly fan-out: learn the advance/reject threshold per org (and agentic
roles) from recruiter terminal decisions, bias-gate it, and write SHADOW
proposals. Auto-apply is opt-in per org and bias-gated; the default is
proposed-for-review (nothing touches live verdicts until a recruiter activates
it in the Decision Hub).
"""
from __future__ import annotations

import logging

from .celery_app import celery_app

logger = logging.getLogger("taali.tasks.threshold_calibration")


@celery_app.task(name="app.tasks.threshold_calibration_tasks.calibrate_thresholds_sweep")
def calibrate_thresholds_sweep() -> dict:
    """Beat-scheduled fan-out across all orgs with terminal-labelled data."""
    from ..platform.database import SessionLocal
    from ..services.threshold_calibration.service import run_for_all_orgs

    db = SessionLocal()
    try:
        summary = run_for_all_orgs(db)
    except Exception:
        db.rollback()
        logger.exception("calibrate_thresholds_sweep failed")
        raise
    finally:
        db.close()
    logger.info(
        "calibrate_thresholds_sweep: orgs=%d org_proposed=%d roles_proposed=%d errors=%d",
        summary.get("orgs", 0),
        summary.get("org_proposed", 0),
        summary.get("roles_proposed", 0),
        summary.get("errors", 0),
    )
    return summary
