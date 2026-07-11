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
from ....platform.secrets import decrypt_text, encrypt_text
from .auth import BullhornAuth
from .service import BullhornService
from .sync_service import BullhornSyncCancelled, BullhornSyncService

logger = logging.getLogger("taali.bullhorn.sync")

# Reuse the shared per-org mutex util; Bullhorn gets its own key namespace so a
# Bullhorn sync/write and a Workable sync/write for the same org don't contend.
BULLHORN_ORG_MUTEX_NAMESPACE = "celery:lock:bullhorn_org_sync"


def _org_connected(org: Organization | None) -> bool:
    return bool(
        org
        and org.bullhorn_connected
        and org.bullhorn_client_id
        and org.bullhorn_refresh_token
        and org.bullhorn_username
    )


def _make_persist_hook(org_id: int):
    """A ``persist_tokens`` hook that re-encrypts + durably writes the rotation.

    Opens its OWN short-lived session (separate transaction from the sync's) so
    the rotated refresh token is committed BEFORE the caller adopts the new
    access token — exactly the ordering the rotation invariant requires. Never
    logs the token.
    """

    def _persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        hook_db = SessionLocal()
        try:
            org = hook_db.query(Organization).filter(Organization.id == org_id).first()
            if org is None:
                raise RuntimeError(f"org {org_id} vanished during Bullhorn token rotation")
            org.bullhorn_refresh_token = encrypt_text(refresh_token, settings.SECRET_KEY)
            if rest_url:
                org.bullhorn_rest_url = rest_url
            hook_db.commit()
        finally:
            hook_db.close()

    return _persist


def _build_service(org: Organization) -> BullhornService:
    """Construct an authed :class:`BullhornService` from the org's stored creds."""
    client_secret = decrypt_text(org.bullhorn_client_secret or "", settings.SECRET_KEY)
    refresh_token = decrypt_text(org.bullhorn_refresh_token or "", settings.SECRET_KEY)
    auth = BullhornAuth(
        username=org.bullhorn_username,
        client_id=org.bullhorn_client_id,
        client_secret=client_secret,
        refresh_token=refresh_token or None,
        persist_tokens=_make_persist_hook(org.id),
        rest_url=org.bullhorn_rest_url,
    )
    return BullhornService(auth, client_id=org.bullhorn_client_id)


def execute_bullhorn_sync_run(*, org_id: int, mode: str = "full") -> None:
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
    sync_completed = False
    cancelled = False
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not _org_connected(org):
            logger.info("Bullhorn sync skipped org_id=%s — not connected", org_id)
            return

        mutex_handle = _acquire_mutex(org_id)
        if mutex_handle is None:
            logger.info("Bullhorn sync skipped org_id=%s — another sync/op holds the lock", org_id)
            return

        # Only now do we own the run: this task acquired the lock (or ran
        # unguarded because Redis was down). Any finalization below is ours to
        # do — a task that bailed at the lock check above must NEVER touch the
        # holder's last-sync status or clear its live progress marker.
        lock_owned = True

        service = BullhornSyncService(_build_service(org))
        try:
            service.sync_org(db, org, mode=mode)
            sync_completed = True
        except BullhornSyncCancelled:
            cancelled = True
            logger.info("Bullhorn sync cancelled org_id=%s", org_id)
    except Exception:
        logger.exception("Bullhorn background sync failed org_id=%s", org_id)
    finally:
        try:
            # Guard finalization on lock ownership: only the task that acquired
            # the lock finalizes. A duplicate task that returned at the lock
            # check must not mark the live run failed or clear its progress.
            if lock_owned:
                _finalize(db, org_id, completed=sync_completed, cancelled=cancelled)
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
        # Redis unavailable — the util contract is "run unguarded". Return the
        # sentinel so the sync proceeds and release is a no-op.
        return _NO_LOCK
    return handle


def _release_mutex(handle) -> None:
    if handle is _NO_LOCK:
        return
    from ....tasks.assessment_tasks import _release_workable_org_mutex

    _release_workable_org_mutex(handle)


# Sentinel for "ran unguarded because Redis was unavailable" — not a real lock,
# so release is a no-op, but the sync still ran (matching the util's fail-open).
_NO_LOCK = object()


def _finalize(db: Session, org_id: int, *, completed: bool, cancelled: bool) -> None:
    """Stamp the org's last-sync status/summary from the final progress JSON."""
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org is None:
            return
        progress = org.bullhorn_sync_progress if isinstance(org.bullhorn_sync_progress, dict) else {}
        if cancelled:
            status = "cancelled"
        elif completed:
            status = "success"
        else:
            status = "failed"
        org.bullhorn_last_sync_at = datetime.now(timezone.utc)
        org.bullhorn_last_sync_status = status
        org.bullhorn_last_sync_summary = {**progress, "status": status}
        # Clear the live progress marker so the UI stops showing an in-flight run.
        org.bullhorn_sync_progress = None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Bullhorn sync finalization failed org_id=%s", org_id)
