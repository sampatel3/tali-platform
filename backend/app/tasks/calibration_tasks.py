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

from sqlalchemy import func

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


_PRESCREEN_SAMPLE_LIMIT = 50


@celery_app.task(name="app.tasks.calibration_tasks.sample_prescreen_for_calibration")
def sample_prescreen_for_calibration(limit: int = _PRESCREEN_SAMPLE_LIMIT) -> dict:
    """Shadow-score a random sample of pre-screen rejects to build
    reject-inference training data for the pre-screen calibrator. Backend-only:
    results are stored in ``prescreen_calibration_samples`` and never written
    to the application or shown to a recruiter. Returns the run summary."""
    from ..services.prescreen_calibration import sample_and_shadow_score_rejects
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        summary = sample_and_shadow_score_rejects(db, limit=limit)
    except Exception:
        db.rollback()
        logger.exception("sample_prescreen_for_calibration failed")
        raise
    finally:
        db.close()
    logger.info("sample_prescreen_for_calibration: %s", summary)
    return summary


@celery_app.task(name="app.tasks.calibration_tasks.recalibrate_cv_match")
def recalibrate_cv_match(lookback_days: int = 90) -> dict:
    """Refit the cv_match calibrators from the latest (score -> outcome) pairs
    (recruiter overrides + realized Workable outcomes). Runs nightly AFTER the
    terminal-scoring task so fresh scores are included.

    NOTE: calibrator snapshots are written to local disk. On the single scoring
    worker this co-locates them with where ``apply_calibrator`` reads at scoring
    time and they're refit nightly, but they are NOT durable across deploys
    (graceful fallback: scoring uses the raw score until the next refit). A
    durable/shared snapshot store (S3/Tigris) is the follow-up to make this
    deploy-proof and multi-worker-safe.
    """
    from ..cv_matching.calibrators.recalibrate import recalibrate_all

    reports = recalibrate_all(lookback_days=lookback_days)
    summary = {
        "fits": len(reports),
        "role_families": sorted({getattr(r, "role_family", "?") for r in reports}),
        "alerts": [
            {
                "role_family": getattr(r, "role_family", "?"),
                "dimension": getattr(r, "dimension", "?"),
                "ece": getattr(r, "ece", None),
            }
            for r in reports
            if (getattr(r, "ece", None) or 0) > 0.05
        ],
    }
    logger.info("recalibrate_cv_match: %s", summary)
    return summary


@celery_app.task(name="app.tasks.calibration_tasks.recalibrate_prescreen_gate")
def recalibrate_prescreen_gate() -> dict:
    """Recompute the data-driven Stage-1 gate threshold per org and log the
    divergence vs the static env threshold.

    SHADOW measurement — changes nothing live (the gate enforces the static
    threshold until ``PRE_SCREEN_DYNAMIC_GATE_ENFORCE`` is on). Runs weekly,
    after ``sample_prescreen_for_calibration`` so the latest shadow rejects are
    included. The recommendation itself is read live (TTL-cached) by the gate;
    this job is the recalibration heartbeat + the false-reject report.
    """
    from ..models.role import Role
    from ..platform.config import settings
    from ..platform.database import SessionLocal
    from ..services.prescreen_gate_calibration import compute_gate_threshold

    db = SessionLocal()
    results: list[dict] = []
    try:
        # One representative live role per org (the cut is org-wide).
        org_rows = (
            db.query(Role.organization_id, func.min(Role.id))
            .filter(Role.deleted_at.is_(None))
            .group_by(Role.organization_id)
            .all()
        )
        for org_id, role_id in org_rows:
            role = db.query(Role).filter(Role.id == role_id).one_or_none()
            if role is None:
                continue
            rec = compute_gate_threshold(db, role=role)
            entry = {"organization_id": int(org_id), "static": int(settings.PRE_SCREEN_THRESHOLD), **rec.to_dict()}
            results.append(entry)
            logger.info(
                "recalibrate_prescreen_gate org=%s static=%s dynamic=%s source=%s "
                "fr_rate=%s filtered_frac=%s n=%s n_pos=%s enforce=%s",
                org_id, entry["static"], rec.value, rec.source, rec.fr_rate,
                rec.filtered_frac, rec.sample_size, rec.n_positive,
                settings.PRE_SCREEN_DYNAMIC_GATE_ENFORCE,
            )
    except Exception:
        logger.exception("recalibrate_prescreen_gate failed")
        raise
    finally:
        db.close()
    return {"orgs": len(results), "enforced": bool(settings.PRE_SCREEN_DYNAMIC_GATE_ENFORCE), "results": results}
