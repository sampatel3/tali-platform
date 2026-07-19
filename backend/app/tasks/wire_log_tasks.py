"""Scheduled retention for Anthropic wire diagnostics."""
from __future__ import annotations

from .celery_app import celery_app
from ..platform.config import settings
from ..platform.database import SessionLocal


@celery_app.task(name="app.tasks.wire_log_tasks.prune_anthropic_wire_logs")
def prune_anthropic_wire_logs() -> dict:
    from ..services.anthropic_wire_retention import prune_wire_logs

    with SessionLocal() as db:
        return prune_wire_logs(
            db, retention_days=settings.ANTHROPIC_WIRE_LOG_RETENTION_DAYS
        )


__all__ = ["prune_anthropic_wire_logs"]
