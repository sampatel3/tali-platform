"""Scheduled aggregate compliance monitors (no candidate-state mutations)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .celery_app import celery_app


logger = logging.getLogger("taali.tasks.compliance")


@celery_app.task(
    name="app.tasks.compliance_tasks.audit_prescreen_adverse_impact",
)
def audit_prescreen_adverse_impact() -> dict:
    """Run the rolling pre-screen impact audit for orgs with voluntary EEO data.

    The task is scheduled daily but is a cheap no-op until the explicit monitor
    flag is enabled.  It reads scoring outcomes and writes only suppressed
    aggregate audit rows; it cannot change a score, verdict, or application.
    """

    from ..decision_policy.bias_audit import load_thresholds
    from ..domains.compliance.prescreen_impact_service import (
        prescreen_audit_organization_ids,
        run_prescreen_adverse_impact_audit,
    )
    from ..platform.config import settings
    from ..platform.database import SessionLocal

    if not settings.PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED:
        return {"status": "disabled", "audited": 0, "violations": 0}

    # Closed UTC-day windows make retries idempotent and avoid auditing a
    # partially collected current day differently on every invocation.
    window_end = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window_start = window_end - timedelta(
        days=int(settings.PRESCREEN_ADVERSE_IMPACT_LOOKBACK_DAYS)
    )
    impact_ratio_min = float(load_thresholds().disparate_impact_ratio_min)

    db = SessionLocal()
    audited = 0
    violation_count = 0
    insufficient = 0
    errors = 0
    try:
        organization_ids = prescreen_audit_organization_ids(db)
        for organization_id in organization_ids:
            try:
                audit = run_prescreen_adverse_impact_audit(
                    db,
                    organization_id=organization_id,
                    window_start=window_start,
                    window_end=window_end,
                    impact_ratio_min=impact_ratio_min,
                    min_cell_n=int(
                        settings.PRESCREEN_ADVERSE_IMPACT_MIN_CELL_N
                    ),
                )
                db.commit()
                audited += 1
                violation_count += len(audit.violations_json or [])
                insufficient += int(audit.status == "insufficient_data")
            except Exception:
                db.rollback()
                errors += 1
                logger.exception(
                    "pre-screen adverse-impact audit failed organization_id=%s",
                    organization_id,
                )
        return {
            "status": "ok" if not errors else "partial",
            "audited": audited,
            "violations": violation_count,
            "insufficient_data": insufficient,
            "errors": errors,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
    finally:
        db.close()


__all__ = ["audit_prescreen_adverse_impact"]
