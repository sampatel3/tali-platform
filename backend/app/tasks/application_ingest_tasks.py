"""Celery dispatch and recovery sweep for ATS application-created intents."""

from __future__ import annotations

import logging

from .celery_app import celery_app


logger = logging.getLogger("taali.tasks.application_ingest")


def _exception_type(exc: Exception) -> str:
    name = type(exc).__name__
    return name[:128] if name.replace("_", "").isalnum() else "Exception"


def _error_result(error_code: str, exc: Exception, **fields: int | str) -> dict:
    return {
        "status": "error",
        **fields,
        "error_code": error_code,
        "error_type": _exception_type(exc),
    }


@celery_app.task(
    name="app.tasks.application_ingest_tasks.dispatch_application_created_outbox",
    max_retries=0,
)
def dispatch_application_created_outbox(outbox_id: int) -> dict:
    """Drain one committed, idempotent application-created outbox row."""

    from ..platform.database import SessionLocal
    from ..services.ats_application_ingest_outbox import dispatch_one

    db = SessionLocal()
    try:
        result = dispatch_one(db, outbox_id=int(outbox_id))
        logger.info("application-created outbox dispatch: %s", result)
        return result
    except Exception as exc:  # the durable row remains sweepable
        db.rollback()
        logger.error(
            "application-created outbox machinery failed "
            "outbox_id=%s error_code=%s error_type=%s",
            outbox_id,
            "dispatch_failed",
            _exception_type(exc),
        )
        return _error_result(
            "dispatch_failed", exc, outbox_id=int(outbox_id)
        )
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.application_ingest_tasks.sweep_application_created_outbox",
    max_retries=0,
)
def sweep_application_created_outbox(limit: int = 200) -> dict:
    """Recover rows whose post-commit broker kick or worker was lost."""

    from ..platform.database import SessionLocal
    from ..services.ats_application_ingest_outbox import recoverable_ids

    db = SessionLocal()
    try:
        outbox_ids = recoverable_ids(db, limit=max(1, int(limit)))
    except Exception as exc:
        db.rollback()
        logger.error(
            "application-created outbox recovery scan failed "
            "error_code=%s error_type=%s",
            "recovery_scan_failed",
            _exception_type(exc),
        )
        return _error_result(
            "recovery_scan_failed", exc, scanned=0, dispatched=0
        )
    finally:
        db.close()

    dispatched = 0
    errors = 0
    for outbox_id in outbox_ids:
        try:
            dispatch_application_created_outbox.delay(int(outbox_id))
            dispatched += 1
        except Exception as exc:
            errors += 1
            logger.error(
                "application-created recovery kick failed "
                "outbox_id=%s error_code=%s error_type=%s",
                outbox_id,
                "queue_unavailable",
                _exception_type(exc),
            )
    return {
        "status": "ok" if not errors else "partial",
        "scanned": len(outbox_ids),
        "dispatched": dispatched,
        "errors": errors,
    }


@celery_app.task(
    name="app.tasks.application_ingest_tasks.dispatch_application_cv_parse_outbox",
    max_retries=0,
)
def dispatch_application_cv_parse_outbox(outbox_id: int) -> dict:
    """Recover one due/lost ATS CV-parse delivery with fresh authority."""

    from ..platform.database import SessionLocal
    from ..services.ats_cv_parse_outbox import redispatch_cv_parse

    db = SessionLocal()
    try:
        return redispatch_cv_parse(db, outbox_id=int(outbox_id))
    except Exception as exc:
        db.rollback()
        logger.error(
            "CV-parse outbox recovery failed "
            "outbox_id=%s error_code=%s error_type=%s",
            outbox_id,
            "redispatch_failed",
            _exception_type(exc),
        )
        return _error_result(
            "redispatch_failed", exc, outbox_id=int(outbox_id)
        )
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.application_ingest_tasks.sweep_application_cv_parse_outbox",
    max_retries=0,
)
def sweep_application_cv_parse_outbox(limit: int = 200) -> dict:
    """Recover lost broker deliveries and retryable parse results."""

    from ..platform.database import SessionLocal
    from ..services.ats_cv_parse_outbox import recoverable_cv_parse_ids

    db = SessionLocal()
    try:
        outbox_ids = recoverable_cv_parse_ids(db, limit=max(1, int(limit)))
    except Exception as exc:
        db.rollback()
        logger.error(
            "CV-parse outbox recovery scan failed "
            "error_code=%s error_type=%s",
            "recovery_scan_failed",
            _exception_type(exc),
        )
        return _error_result(
            "recovery_scan_failed", exc, scanned=0, dispatched=0
        )
    finally:
        db.close()

    dispatched = 0
    errors = 0
    for outbox_id in outbox_ids:
        try:
            dispatch_application_cv_parse_outbox.delay(int(outbox_id))
            dispatched += 1
        except Exception as exc:
            errors += 1
            logger.error(
                "CV-parse recovery kick failed "
                "outbox_id=%s error_code=%s error_type=%s",
                outbox_id,
                "queue_unavailable",
                _exception_type(exc),
            )
    return {
        "status": "ok" if not errors else "partial",
        "scanned": len(outbox_ids),
        "dispatched": dispatched,
        "errors": errors,
    }


__all__ = [
    "dispatch_application_cv_parse_outbox",
    "dispatch_application_created_outbox",
    "sweep_application_cv_parse_outbox",
    "sweep_application_created_outbox",
]
