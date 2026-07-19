"""Entry point that runs a Bullhorn full sync in a worker context.

Mirrors ``workable/sync_runner.execute_workable_sync_run`` but adds the two
things Bullhorn needs that Workable doesn't:

1. **Credential handling.** The org's ``bullhorn_client_secret`` and
   ``bullhorn_refresh_token`` are Fernet ciphertext; we decrypt with
   ``SECRET_KEY`` to build the client, and supply a ``persist_tokens`` hook that
   RE-ENCRYPTS and durably writes the rotated refresh token (in its own
   transaction) BEFORE the new access token is used — the single-use-rotation
   crash-safety invariant :class:`BullhornAuth` depends on.

2. **The per-org mutex** in the ``bullhorn:{org_id}`` namespace (distinct from
   Workable's), reusing the shared mutex util so a Bullhorn sync and a Bullhorn
   write-back never talk to the API concurrently for one org.

Gating (hard rule): a no-op when ``BULLHORN_ENABLED`` is False or the org has no
Bullhorn connection. Nothing here runs, and no credentials are touched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ....models.organization import Organization
from ....platform.config import settings
from ....platform.database import SessionLocal
from ....platform.secrets import decrypt_integration_secret
from .auth import BullhornAuth
from .bootstrap import (
    CONNECT_BOOTSTRAP_TRIGGER,
    DURABLE_FULL_SYNC_TRIGGERS,
    retry_marker_after_run_failure,
)
from .service import BullhornService
from .sync_service import (
    BullhornSyncCancelled,
    BullhornSyncLeaseLost,
    BullhornSyncService,
)
from .credential_state import credential_generation, persist_rotated_credentials
from .errors import BullhornAuthError

logger = logging.getLogger("taali.bullhorn.sync")

# Reuse the shared per-org mutex util; Bullhorn gets its own key namespace so a
# Bullhorn sync/write and a Workable sync/write for the same org don't contend.
BULLHORN_ORG_MUTEX_NAMESPACE = "celery:lock:bullhorn_org_sync"


class BullhornMutexUnavailable(RuntimeError):
    """Redis lock state is unknown; Bullhorn must retry, never fail open."""


def _org_connected(org: Organization | None) -> bool:
    return bool(
        org
        and org.bullhorn_connected
        and org.bullhorn_client_id
        and org.bullhorn_refresh_token
        and org.bullhorn_username
    )


def _make_persist_hook(org_id: int, expected_generation: int):
    """A ``persist_tokens`` hook that re-encrypts + durably writes the rotation.

    Opens its OWN short-lived session (separate transaction from the sync's) so
    the rotated refresh token is committed BEFORE the caller adopts the new
    access token — exactly the ordering the rotation invariant requires. Never
    logs the token.
    """

    def _persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        persist_rotated_credentials(
            org_id=org_id,
            expected_generation=expected_generation,
            refresh_token=refresh_token,
            rest_url=rest_url,
        )

    return _persist


def _build_service(org: Organization) -> BullhornService:
    """Construct an authed :class:`BullhornService` from the org's stored creds."""
    try:
        client_secret = decrypt_integration_secret(org.bullhorn_client_secret)
        refresh_token = decrypt_integration_secret(org.bullhorn_refresh_token)
    except Exception:
        raise BullhornAuthError(
            "Stored Bullhorn credentials are unavailable; reconnect required"
        ) from None
    auth = BullhornAuth(
        username=org.bullhorn_username,
        client_id=org.bullhorn_client_id,
        client_secret=client_secret,
        refresh_token=refresh_token or None,
        persist_tokens=_make_persist_hook(org.id, credential_generation(org)),
        rest_url=org.bullhorn_rest_url,
    )
    return BullhornService(auth, client_id=org.bullhorn_client_id)


def execute_bullhorn_sync_run(
    *,
    org_id: int,
    mode: str = "full",
    run_id: str | None = None,
    trigger: str | None = None,
) -> None:
    """Run one Bullhorn full sync for an org under the per-org mutex.

    No-op when the flag is off or the org isn't connected. Records a terminal
    status + summary on the org row in a ``finally`` so a crash still leaves a
    readable last-sync state (mirrors the Workable runner's finalization).
    """
    if not settings.BULLHORN_ENABLED:
        logger.info("Bullhorn sync skipped org_id=%s — BULLHORN_ENABLED is off", org_id)
        return

    db = SessionLocal()
    mutex_handle = None
    lock_owned = False
    claimed_run_id: str | None = None
    sync_completed = False
    cancelled = False
    hitl_failure_code: str | None = None
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not _org_connected(org):
            logger.info("Bullhorn sync skipped org_id=%s — not connected", org_id)
            return

        mutex_handle = _acquire_mutex(org_id)
        if mutex_handle is None:
            logger.info("Bullhorn sync skipped org_id=%s — another sync/op holds the lock", org_id)
            return

        # A recovery sweep may dispatch the same durable run more than once.
        # Re-read after acquiring the mutex and refuse a completed/stale id before
        # claiming finalization ownership, so at-least-once delivery is harmless.
        db.expire_all()
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if _tracked_run_is_terminal(org, run_id):
            logger.info(
                "Bullhorn sync duplicate skipped org_id=%s run_id=%s",
                org_id,
                run_id,
            )
            return
        if not _prepare_tracked_run(
            db,
            org,
            run_id=run_id,
            mode=mode,
            trigger=trigger,
        ):
            logger.info(
                "Bullhorn sync stale task skipped org_id=%s run_id=%s",
                org_id,
                run_id,
            )
            return

        claimed_progress = (
            org.bullhorn_sync_progress
            if isinstance(org.bullhorn_sync_progress, dict)
            else {}
        )
        claimed_run_id = str(claimed_progress.get("run_id") or "").strip() or None

        # Only now do we own the run: this task acquired the lock. Any
        # finalization below is ours to do. A task that bailed at the lock check
        # must NEVER touch the holder's status or clear its live progress marker.
        lock_owned = True

        service = BullhornSyncService(_build_service(org))
        try:
            service.sync_org(
                db,
                org,
                mode=mode,
                ownership_lost=lambda: _mutex_ownership_lost(mutex_handle),
            )
            sync_completed = True
        except BullhornSyncCancelled:
            cancelled = True
            logger.info("Bullhorn sync cancelled org_id=%s", org_id)
        except BullhornSyncLeaseLost:
            db.rollback()
            logger.warning(
                "Bullhorn sync lease lost org_id=%s; stopping before another provider call",
                org_id,
            )
    except BullhornAuthError:
        hitl_failure_code = "bullhorn_reconnect_required"
        logger.error(
            "Bullhorn background sync requires reconnect org_id=%s error_type=BullhornAuthError",
            org_id,
        )
    except BullhornMutexUnavailable:
        logger.warning("Bullhorn mutex unavailable org_id=%s; sync will retry", org_id)
        raise
    except Exception as exc:
        logger.error(
            "Bullhorn background sync failed org_id=%s error_type=%s",
            org_id,
            type(exc).__name__,
        )
    finally:
        try:
            # Guard finalization on lock ownership: only the task that acquired
            # the lock finalizes. A duplicate task that returned at the lock
            # check must not mark the live run failed or clear its progress.
            if lock_owned:
                _finalize(
                    db,
                    org_id,
                    completed=sync_completed,
                    cancelled=cancelled,
                    hitl_failure_code=hitl_failure_code,
                    expected_run_id=claimed_run_id,
                    mutex_handle=mutex_handle,
                )
        finally:
            if mutex_handle is not None:
                _release_mutex(mutex_handle)
            db.close()


def _acquire_mutex(org_id: int):
    """Acquire the Bullhorn per-org mutex (heartbeat, distinct namespace)."""
    from ....tasks.assessment_tasks import _acquire_workable_org_mutex

    handle = _acquire_workable_org_mutex(
        org_id,
        source="bullhorn_sync",
        heartbeat=True,
        namespace=BULLHORN_ORG_MUTEX_NAMESPACE,
    )
    if handle is None:
        # Held by another Bullhorn sync/op for this org — skip this fire.
        return None
    if handle is False:
        raise BullhornMutexUnavailable(
            f"Bullhorn mutex state unavailable for org {org_id}"
        )
    return handle


def _release_mutex(handle) -> None:
    from ....tasks.assessment_tasks import _release_workable_org_mutex

    _release_workable_org_mutex(handle)


def _mutex_ownership_lost(handle) -> bool:
    from ....tasks.workable_mutex import _workable_mutex_ownership_lost

    return _workable_mutex_ownership_lost(handle)


def _mutex_is_owned(handle) -> bool:
    from ....tasks.workable_mutex import _workable_mutex_is_owned

    return _workable_mutex_is_owned(handle)


def _tracked_run_is_terminal(org: Organization | None, run_id: str | None) -> bool:
    if org is None or not run_id:
        return False
    progress = org.bullhorn_sync_progress
    if isinstance(progress, dict) and str(progress.get("run_id") or "") == run_id:
        if progress.get("phase") not in {"completed", "failed", "cancelled"}:
            return False
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    if str(config.get("initial_full_sync_run_id") or "") == run_id:
        return str(config.get("initial_full_sync_status") or "") in {
            "success",
            "failed",
            "cancelled",
        }
    summary = (
        org.bullhorn_last_sync_summary
        if isinstance(org.bullhorn_last_sync_summary, dict)
        else {}
    )
    return bool(
        str(summary.get("run_id") or "") == run_id
        and str(summary.get("status") or "") in {"success", "failed", "cancelled"}
    )


def _prepare_tracked_run(
    db: Session,
    org: Organization | None,
    *,
    run_id: str | None,
    mode: str,
    trigger: str | None,
) -> bool:
    """Attach task metadata without overwriting a different live run."""
    if org is None or not run_id:
        return org is not None
    current = (
        org.bullhorn_sync_progress
        if isinstance(org.bullhorn_sync_progress, dict)
        else {}
    )
    active_id = str(current.get("run_id") or "")
    if (
        active_id
        and active_id != run_id
        and current.get("phase") not in {"completed", "failed", "cancelled"}
    ):
        return False
    marker = dict(current) if active_id == run_id else {}
    marker.update(
        {
            "phase": marker.get("phase") or "queued",
            "mode": mode,
            "run_id": run_id,
            "trigger": trigger or marker.get("trigger") or "tracked_sync",
            "cancel_requested": bool(marker.get("cancel_requested", False)),
        }
    )
    if marker.get("trigger") in DURABLE_FULL_SYNC_TRIGGERS:
        marker["run_attempts"] = int(marker.get("run_attempts") or 0) + 1
    org.bullhorn_sync_progress = marker
    db.add(org)
    db.commit()
    return True


def _finalize(
    db: Session,
    org_id: int,
    *,
    completed: bool,
    cancelled: bool,
    hitl_failure_code: str | None = None,
    expected_run_id: str | None,
    mutex_handle,
) -> bool:
    """Stamp the org's last-sync status/summary from the final progress JSON."""
    try:
        org = (
            db.query(Organization)
            .filter(Organization.id == org_id)
            .with_for_update(of=Organization)
            .populate_existing()
            .first()
        )
        if org is None:
            db.rollback()
            return False
        progress = org.bullhorn_sync_progress if isinstance(org.bullhorn_sync_progress, dict) else {}
        current_run_id = str(progress.get("run_id") or "").strip() or None
        if current_run_id != expected_run_id:
            db.rollback()
            logger.warning(
                "Bullhorn sync finalization superseded org_id=%s",
                org_id,
            )
            return False
        if cancelled:
            status = "cancelled"
        elif completed:
            status = "success"
        else:
            status = "failed"
        finished_at = datetime.now(timezone.utc)
        org.bullhorn_last_sync_at = finished_at
        org.bullhorn_last_sync_status = status
        org.bullhorn_last_sync_summary = {
            **progress,
            "status": status,
            **(
                {
                    "requires_human_action": True,
                    "failure_code": hitl_failure_code,
                }
                if hitl_failure_code
                else {}
            ),
        }
        retry_marker = (
            retry_marker_after_run_failure(progress, now=finished_at)
            if status == "failed" and not hitl_failure_code
            else None
        )
        org.bullhorn_sync_progress = retry_marker
        if (
            progress.get("trigger") == CONNECT_BOOTSTRAP_TRIGGER
            and retry_marker is None
            and progress.get("run_id")
        ):
            config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
            config.pop("initial_sync_bootstrap", None)
            config.update(
                {
                    "initial_full_sync_run_id": str(progress["run_id"]),
                    "initial_full_sync_status": status,
                    "initial_full_sync_finished_at": finished_at.isoformat(),
                }
            )
            org.bullhorn_config = config
        db.flush()
        # The row lock prevents a replacement worker from committing newer run
        # state between this exact Redis-token check and our commit. If the
        # replacement already owns the lease, or Redis cannot prove ownership,
        # roll back every terminal mutation and let the current owner finish.
        if not _mutex_is_owned(mutex_handle):
            db.rollback()
            logger.warning(
                "Bullhorn sync finalization lease fence failed org_id=%s",
                org_id,
            )
            return False
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error(
            "Bullhorn sync finalization failed org_id=%s error_type=%s",
            org_id,
            type(exc).__name__,
        )
        return False
