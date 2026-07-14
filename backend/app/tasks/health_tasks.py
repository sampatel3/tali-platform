"""End-to-end Celery queue canaries used by production activation checks."""

from __future__ import annotations

from .celery_app import celery_app


@celery_app.task(name="app.tasks.health_tasks.queue_worker_heartbeat")
def queue_worker_heartbeat(queue_name: str) -> dict:
    """Record that a worker consumed a canary from ``queue_name``.

    Beat routes one invocation to each required queue. The queue name is also
    carried in the payload so a task delivered through the wrong route cannot
    accidentally certify another queue.
    """
    from ..services.agent_worker_health import (
        provider_probe_status,
        record_heartbeat,
        runtime_capabilities,
    )

    capabilities = runtime_capabilities()
    capabilities.update(provider_probe_status(queue_name))
    recorded_at = record_heartbeat(queue_name, capabilities=capabilities)
    return {
        "status": "ok",
        "queue": queue_name,
        "recorded_at_epoch": recorded_at,
        "capabilities": capabilities,
    }


@celery_app.task(name="app.tasks.health_tasks.release_stale_usage_credit_reservations")
def release_stale_usage_credit_reservations(
    stale_after_minutes: int = 120,
    limit: int = 500,
) -> dict:
    """Reconcile billable provider holds and refund only pre-call orphans."""
    from ..platform.database import SessionLocal
    from ..services.usage_credit_reservation_recovery import (
        release_stale_credit_reservations,
    )

    with SessionLocal() as db:
        result = release_stale_credit_reservations(
            db,
            stale_after_minutes=int(stale_after_minutes),
            limit=int(limit),
        )
        db.commit()
        return result


__all__ = [
    "queue_worker_heartbeat",
    "release_stale_usage_credit_reservations",
]
