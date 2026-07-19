"""Batch-scoped cancellation checks for individual score workers."""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)
_TERMINAL_RUN_STATUSES = frozenset({"completed", "cancelled", "failed"})


def score_job_cancel_requested(db, job, *, role_id: int | None) -> bool:
    """Return true when this exact job's durable owner revoked authority.

    New batch-owned work never consults the historical role-wide Redis flag,
    which could cancel unrelated manual/autonomous jobs.  Unbound rows retain
    that legacy compatibility signal for in-flight work created before the
    ownership migration.
    """

    batch_run_id = getattr(job, "batch_run_id", None)
    if batch_run_id is not None:
        try:
            from ..models.background_job_run import BackgroundJobRun

            row = (
                db.query(
                    BackgroundJobRun.status,
                    BackgroundJobRun.finished_at,
                    BackgroundJobRun.cancel_requested_at,
                )
                .filter(BackgroundJobRun.id == int(batch_run_id))
                .one_or_none()
            )
            if row is None:
                return True
            return bool(
                row.cancel_requested_at is not None
                or row.finished_at is not None
                or str(row.status or "") in _TERMINAL_RUN_STATUSES
                or str(row.status or "") == "cancelling"
            )
        except Exception:
            logger.exception(
                "score batch cancellation read failed run_id=%s — blocked",
                batch_run_id,
            )
            return True

    if role_id is None:
        return False
    try:
        from ..domains.assessments_runtime.applications_routes import (
            is_batch_score_cancelled,
        )

        return bool(is_batch_score_cancelled(int(role_id)))
    except Exception:  # pragma: no cover - legacy Redis is best effort
        return False


__all__ = ["score_job_cancel_requested"]
