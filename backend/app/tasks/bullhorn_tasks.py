"""Celery tasks for the Bullhorn integration.

Mirrors ``workable_tasks`` + the Workable sweep tasks in ``assessment_tasks``.
Holds:

* ``run_bullhorn_sync_run_task`` — on-demand full sync for one org (connect-time
  + manual resync).
* ``bullhorn_event_poll_sweep`` — beat task: fan an event-queue drain to every
  connected org on the ``BULLHORN_EVENT_POLL_SECONDS`` cadence (default 180s).
* ``bullhorn_reconcile_sweep`` — nightly beat task: the ``dateLastModified``
  fallback sweep + count reconciliation per connected org.

Gating (hard rule): every task is a cheap early exit when ``BULLHORN_ENABLED``
is off — the sweep returns before touching the DB. Per-org, the runners re-check
the connection and no-op if the org isn't connected. So these are safe to leave
on the beat schedule in any environment; on the live platform (flag off) they do
nothing.

Eager-imported from ``app/tasks/__init__.py`` so the worker registers them — an
unregistered beat task is fired by beat and silently dropped by the worker (see
``tests/test_celery_beat_registration.py``).
"""

import logging

from .celery_app import celery_app
from .retry_safety import raise_secret_safe_task_retry as _retry_safely
from ..components.integrations.bullhorn.incremental_runner import (
    execute_bullhorn_event_poll,
    execute_bullhorn_reconcile,
)
from ..components.integrations.bullhorn.sync_runner import (
    BullhornMutexUnavailable,
    execute_bullhorn_sync_run,
)
from ..platform.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.bullhorn_tasks.run_bullhorn_sync_run",
    max_retries=12,
)
def run_bullhorn_sync_run_task(
    self,
    org_id: int,
    mode: str = "full",
    run_id: str | None = None,
    trigger: str | None = None,
) -> dict:
    """Run one Bullhorn full sync for an org in a background worker context."""
    logger.info(
        "Executing Bullhorn sync task org_id=%s mode=%s run_id=%s",
        org_id,
        mode,
        run_id,
    )
    try:
        execute_bullhorn_sync_run(
            org_id=org_id,
            mode=mode,
            run_id=run_id,
            trigger=trigger,
        )
    except BullhornMutexUnavailable as exc:
        _retry_safely(self, exc, operation="bullhorn_sync_mutex", countdown=60)
    return {"status": "ok", "org_id": org_id, "mode": mode, "run_id": run_id}


@celery_app.task(name="app.tasks.bullhorn_tasks.bullhorn_initial_sync_recovery_sweep")
def bullhorn_initial_sync_recovery_sweep() -> dict:
    """Recover connect-time full syncs lost between DB commit and execution."""
    if not settings.BULLHORN_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    from ..components.integrations.bullhorn.bootstrap import recover_due_initial_syncs

    return recover_due_initial_syncs()


def _connected_org_ids() -> list[int]:
    """Ids of orgs with a live Bullhorn connection. Empty when the flag is off.

    A dedicated short-lived session; the per-org runners open their own. Cheap:
    one indexed boolean filter. Returns [] on any error so a sweep never raises.
    """
    from ..models.organization import Organization
    from ..platform.database import SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(Organization.id)
            .filter(
                Organization.bullhorn_connected == True,  # noqa: E712
                Organization.bullhorn_refresh_token.isnot(None),
                Organization.bullhorn_username.isnot(None),
            )
            .all()
        )
        return [int(r[0]) for r in rows]
    except Exception as exc:  # pragma: no cover — never let a sweep die on the org query
        logger.error(
            "Bullhorn: failed to list connected orgs error_type=%s",
            type(exc).__name__,
        )
        return []
    finally:
        db.close()


@celery_app.task(name="app.tasks.bullhorn_tasks.bullhorn_event_poll_sweep")
def bullhorn_event_poll_sweep() -> dict:
    """Beat task: drain + process the event queue for every connected org.

    Cheap early exit when ``BULLHORN_ENABLED`` is off (the live-platform default)
    — returns before any DB work. Otherwise fans a per-org event poll; each org's
    runner is independently gated + mutex-guarded, so one locked/erroring org
    never blocks the others.
    """
    if not settings.BULLHORN_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    org_ids = _connected_org_ids()
    if not org_ids:
        return {"status": "ok", "orgs": 0}

    polled = 0
    retry_pending = 0
    skipped = 0
    failed = 0
    for org_id in org_ids:
        try:
            result = execute_bullhorn_event_poll(org_id=org_id)
            status = result.get("status")
            if status == "ok":
                polled += 1
            elif status == "retry_pending":
                retry_pending += 1
            elif status == "error":
                failed += 1
            else:
                skipped += 1
        except Exception as exc:  # pragma: no cover — isolate a bad org
            failed += 1
            logger.error(
                "Bullhorn event poll task failed org_id=%s error_type=%s",
                org_id,
                type(exc).__name__,
            )
    return {
        "status": "degraded" if retry_pending or failed else "ok",
        "polled": polled,
        "retry_pending": retry_pending,
        "skipped": skipped,
        "failed": failed,
    }


@celery_app.task(name="app.tasks.bullhorn_tasks.bullhorn_reconcile_sweep")
def bullhorn_reconcile_sweep() -> dict:
    """Nightly beat task: fallback sweep + count reconciliation per connected org.

    Same cheap early exit + per-org gating as the event-poll sweep. Read-mostly
    (the sweep upserts via the shared gated path; reconciliation only counts +
    records discrepancies), so it never triggers paid re-evaluation.
    """
    if not settings.BULLHORN_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    org_ids = _connected_org_ids()
    if not org_ids:
        return {"status": "ok", "orgs": 0}

    reconciled = 0
    retry_pending = 0
    skipped = 0
    failed = 0
    for org_id in org_ids:
        try:
            result = execute_bullhorn_reconcile(org_id=org_id)
            status = result.get("status")
            if status == "ok":
                reconciled += 1
            elif status == "retry_pending":
                retry_pending += 1
            elif status == "error":
                failed += 1
            else:
                skipped += 1
        except Exception as exc:  # pragma: no cover — isolate a bad org
            failed += 1
            logger.error(
                "Bullhorn reconcile task failed org_id=%s error_type=%s",
                org_id,
                type(exc).__name__,
            )
    return {
        "status": "degraded" if retry_pending or failed else "ok",
        "reconciled": reconciled,
        "retry_pending": retry_pending,
        "skipped": skipped,
        "failed": failed,
    }
