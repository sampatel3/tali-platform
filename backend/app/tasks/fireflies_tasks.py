"""Workers for the durable Fireflies webhook inbox."""
from __future__ import annotations

from .celery_app import celery_app
from ..platform.database import SessionLocal


@celery_app.task(name="app.tasks.fireflies_tasks.process_fireflies_webhook")
def process_fireflies_webhook(inbox_id: int) -> dict:
    from ..services.fireflies_inbox_service import process_one

    with SessionLocal() as db:
        return process_one(db, inbox_id=int(inbox_id))


@celery_app.task(name="app.tasks.fireflies_tasks.sweep_fireflies_webhooks")
def sweep_fireflies_webhooks(limit: int = 100) -> dict:
    """Recover broker loss, cooled-down retries, and expired worker leases."""
    from ..services.fireflies_inbox_service import due_ids

    with SessionLocal() as db:
        ids = due_ids(db, limit=limit)
    for inbox_id in ids:
        process_fireflies_webhook.delay(inbox_id)
    return {"status": "ok", "dispatched": len(ids)}


__all__ = ["process_fireflies_webhook", "sweep_fireflies_webhooks"]
