"""Worker entry points for the Bullhorn incremental sync + reconciliation.

Mirrors :mod:`sync_runner` (the full-sync runner) but for the two incremental
paths. Both reuse that module's credential wiring (``_build_service``, which
installs the rotation-safe ``persist_tokens`` hook), connection gate
(``_org_connected``), and per-org mutex (``bullhorn:{org_id}`` namespace) so an
event poll and a full sync / write-back for one org never hit the API
concurrently.

Two entry points:
* :func:`execute_bullhorn_event_poll` — ensure the subscription, poll+process the
  destructive queue (checkpoint-before-processing), and run a gap-covering sweep
  whenever the subscription had to be (re)created.
* :func:`execute_bullhorn_reconcile` — the nightly ``dateLastModified`` fallback
  sweep + count-based reconciliation.

Hard gate (from the build plan): both no-op when ``BULLHORN_ENABLED`` is False or
the org isn't connected. Nothing runs and no credentials are touched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ....models.organization import Organization
from ....platform.config import settings
from ....platform.database import SessionLocal
from . import events, reconcile
from .sync_runner import (
    _acquire_mutex,
    _build_service,
    _org_connected,
    _release_mutex,
)

logger = logging.getLogger("taali.bullhorn.incremental")

# How far back the nightly fallback sweep looks when the org has no prior
# incremental watermark yet (first reconcile after connect). A day comfortably
# covers the gap between the connect-time full sync and the first nightly run.
_DEFAULT_SWEEP_LOOKBACK = timedelta(days=1)
# Safety overlap subtracted from the watermark so a change landing right at the
# boundary of the last run isn't missed (dateLastModified ordering is undocumented).
_SWEEP_OVERLAP = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_connected_org(db: Session, org_id: int) -> Organization | None:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return org if _org_connected(org) else None


def execute_bullhorn_event_poll(*, org_id: int) -> dict:
    """Ensure subscription + drain/process the event queue for one org.

    No-op when the flag is off or the org isn't connected. Runs under the per-org
    mutex so it never races the full sync or a write-back. On a (re)created
    subscription, runs a gap-covering ``dateLastModified`` sweep first, since the
    fresh queue misses anything that changed during the gap.
    """
    if not settings.BULLHORN_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    db = SessionLocal()
    mutex_handle = None
    try:
        org = _load_connected_org(db, org_id)
        if org is None:
            return {"status": "skipped", "reason": "not_connected"}

        mutex_handle = _acquire_mutex(org_id)
        if mutex_handle is None:
            # Another Bullhorn sync/op holds the lock — skip this fire; the next
            # beat tick retries.
            return {"status": "skipped", "reason": "locked"}

        client = _build_service(org)
        sub_id, created = events.ensure_subscription(db, org, client=client)
        result: dict = {"status": "ok", "subscription_id": sub_id, "created": created}

        if created:
            # First subscription for this org: its queue starts empty, so anything
            # changed before it existed is invisible to events. Backfill via a
            # gap-covering sweep before polling.
            result["gap_sweep"] = _gap_sweep(db, org, client=client)

        poll = events.poll_and_process_events(db, org, client=client)
        if poll.get("status") == "subscription_dead":
            # The subscription expired / vanished (≤30-day lifetime). Recreate it
            # and run a gap-covering sweep to cover the outage window the dead
            # subscription missed, then poll the fresh (initially empty) queue.
            events.recreate_subscription(db, org, client=client)
            result["recreated"] = True
            result["gap_sweep"] = _gap_sweep(db, org, client=client)
            poll = events.poll_and_process_events(db, org, client=client)
        result["poll"] = poll
        _stamp_incremental(db, org)
        return result
    except Exception:
        logger.exception("Bullhorn event poll failed org_id=%s", org_id)
        return {"status": "error", "org_id": org_id}
    finally:
        if mutex_handle is not None:
            _release_mutex(mutex_handle)
        db.close()


def execute_bullhorn_reconcile(*, org_id: int) -> dict:
    """Nightly fallback sweep + count reconciliation for one org.

    No-op when the flag is off or the org isn't connected. Runs the
    ``dateLastModified`` sweep from the last-incremental watermark (with overlap),
    then the count-based reconciliation, both under the per-org mutex.
    """
    if not settings.BULLHORN_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    db = SessionLocal()
    mutex_handle = None
    try:
        org = _load_connected_org(db, org_id)
        if org is None:
            return {"status": "skipped", "reason": "not_connected"}

        mutex_handle = _acquire_mutex(org_id)
        if mutex_handle is None:
            return {"status": "skipped", "reason": "locked"}

        client = _build_service(org)
        since = _sweep_watermark(org)
        sweep = reconcile.sweep_modified_since(db, org, client=client, since=since)
        counts = reconcile.reconcile_counts(db, org, client=client)
        _stamp_incremental(db, org)
        return {"status": "ok", "sweep": sweep, "reconciliation": counts}
    except Exception:
        logger.exception("Bullhorn reconcile failed org_id=%s", org_id)
        return {"status": "error", "org_id": org_id}
    finally:
        if mutex_handle is not None:
            _release_mutex(mutex_handle)
        db.close()


def _gap_sweep(db: Session, org: Organization, *, client) -> dict:
    """Run a gap-covering ``dateLastModified`` sweep from the sweep watermark.

    Used when a subscription is first created or recreated after expiry — the
    fresh queue misses anything changed during the gap, so we sweep from the last
    known incremental watermark (or the default lookback on the very first run).
    """
    return reconcile.sweep_modified_since(db, org, client=client, since=_sweep_watermark(org))


def _sweep_watermark(org: Organization) -> datetime:
    """The 'modified since' floor for the fallback sweep.

    Uses the last incremental run stamp minus an overlap so a change at the
    boundary isn't skipped; falls back to a default lookback on the first run.
    """
    summary = org.bullhorn_last_sync_summary if isinstance(org.bullhorn_last_sync_summary, dict) else {}
    last = summary.get("last_incremental_at")
    if isinstance(last, str) and last:
        try:
            parsed = datetime.fromisoformat(last)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed - _SWEEP_OVERLAP
        except ValueError:  # pragma: no cover — malformed stamp, fall back
            pass
    return _now() - _DEFAULT_SWEEP_LOOKBACK


def _stamp_incremental(db: Session, org: Organization) -> None:
    """Record the last-incremental run time on the summary (sweep watermark source)."""
    summary = org.bullhorn_last_sync_summary if isinstance(org.bullhorn_last_sync_summary, dict) else {}
    org.bullhorn_last_sync_summary = {**summary, "last_incremental_at": _now().isoformat()}
    db.add(org)
    db.commit()
