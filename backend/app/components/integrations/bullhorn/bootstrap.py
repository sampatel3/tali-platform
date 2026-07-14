"""Durable connect-time Bullhorn full-sync bootstrap.

The Bullhorn connection transaction stores a small, credential-free outbox
marker in ``Organization.bullhorn_sync_progress``.  Dispatch happens only after
that transaction commits.  If the web process or broker fails between commit
and task execution, a Celery beat recovery sweep re-dispatches the same
``run_id``.  The full-sync runner treats that id idempotently, so an at-least-once
dispatch cannot turn into duplicate completed runs.

No new table is required: Bullhorn already owns this org-scoped progress JSON,
and the normal sync replaces the queued marker with live progress before
finalizing it into ``bullhorn_last_sync_summary``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ....models.organization import Organization
from ....platform.database import SessionLocal

logger = logging.getLogger("taali.bullhorn.bootstrap")

CONNECT_BOOTSTRAP_TRIGGER = "connect_bootstrap"
MANUAL_FULL_SYNC_TRIGGER = "manual_full_sync"
DURABLE_FULL_SYNC_TRIGGERS = {
    CONNECT_BOOTSTRAP_TRIGGER,
    MANUAL_FULL_SYNC_TRIGGER,
}
_DISPATCH_LEASE = timedelta(minutes=10)
_LIVE_PROGRESS_LEASE = timedelta(hours=2)


@dataclass(frozen=True)
class InitialSyncIntent:
    """The durable run prepared inside the connection transaction."""

    run_id: str
    should_dispatch: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _new_bootstrap_marker(
    *,
    trigger: str = CONNECT_BOOTSTRAP_TRIGGER,
    mode: str = "full",
) -> dict:
    queued_at = _now().isoformat()
    return {
        "phase": "queued",
        "mode": mode,
        "trigger": trigger,
        "run_id": str(uuid.uuid4()),
        "queued_at": queued_at,
        "dispatch_status": "pending",
        "dispatch_attempts": 0,
        "run_attempts": 0,
        "recover_after": queued_at,
        "cancel_requested": False,
    }


def _pending_bootstrap(org: Organization) -> dict | None:
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    marker = config.get("initial_sync_bootstrap")
    if not isinstance(marker, dict):
        return None
    if marker.get("trigger") != CONNECT_BOOTSTRAP_TRIGGER or not marker.get("run_id"):
        return None
    return dict(marker)


def _store_pending_bootstrap(org: Organization, marker: dict) -> None:
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    config["initial_sync_bootstrap"] = marker
    org.bullhorn_config = config


def _clear_pending_bootstrap(org: Organization) -> None:
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    if "initial_sync_bootstrap" in config:
        config.pop("initial_sync_bootstrap", None)
        org.bullhorn_config = config


def prepare_initial_full_sync(org: Organization) -> InitialSyncIntent:
    """Persist-ready initial FULL-sync intent; caller commits it with creds.

    Reconnecting while a full sync is already live must not overwrite that
    runner's progress.  We attach a run id if it lacks one and let that existing
    run satisfy the bootstrap instead of dispatching a competing walk.
    """
    current = org.bullhorn_sync_progress
    pending = _pending_bootstrap(org)
    if (
        isinstance(current, dict)
        and current.get("phase") not in {
            "completed",
            "failed",
            "cancelled",
        }
        and _progress_is_fresh(current)
    ):
        if str(current.get("mode") or "").strip().lower() == "full":
            # This full walk already satisfies bootstrap. Add only a tracking id
            # when absent; do not relabel or otherwise mutate an unrelated run.
            updated = dict(current)
            run_id = str(updated.get("run_id") or uuid.uuid4())
            updated["run_id"] = run_id
            org.bullhorn_sync_progress = updated
            if updated.get("trigger") == CONNECT_BOOTSTRAP_TRIGGER:
                _clear_pending_bootstrap(org)
                return InitialSyncIntent(
                    run_id=run_id,
                    should_dispatch=updated.get("phase") == "queued",
                )
            tracker = pending or _new_bootstrap_marker()
            tracker = {
                **tracker,
                "phase": "watching_active_full",
                "run_id": run_id,
                "recover_after": _now().isoformat(),
            }
            _store_pending_bootstrap(org, tracker)
            return InitialSyncIntent(run_id=run_id, should_dispatch=False)

        # An incremental/metadata run cannot satisfy the required historical
        # import. Keep its live progress untouched and store a separate pending
        # FULL outbox intent for recovery to promote once that run releases.
        marker = pending or _new_bootstrap_marker()
        marker = {
            **marker,
            "phase": "waiting_for_active_sync",
            "recover_after": _now().isoformat(),
        }
        _store_pending_bootstrap(org, marker)
        return InitialSyncIntent(run_id=str(marker["run_id"]), should_dispatch=False)

    if isinstance(current, dict) and current.get("phase") not in {
        "completed",
        "failed",
        "cancelled",
    }:
        logger.warning(
            "Replacing stale Bullhorn sync progress during reconnect org_id=%s phase=%s",
            org.id,
            current.get("phase"),
        )

    marker = pending or _new_bootstrap_marker()
    marker = {
        **marker,
        "phase": "queued",
        "recover_after": _now().isoformat(),
    }
    org.bullhorn_sync_progress = marker
    _clear_pending_bootstrap(org)
    run_id = str(marker["run_id"])
    return InitialSyncIntent(run_id=run_id, should_dispatch=True)


def start_manual_full_sync(
    db: Session,
    org: Organization,
    *,
    mode: str = "full",
) -> dict:
    """Durably dispatch a recruiter-requested sync with Beat recovery."""
    marker = _new_bootstrap_marker(
        trigger=MANUAL_FULL_SYNC_TRIGGER,
        mode=mode,
    )
    org.bullhorn_sync_progress = marker
    db.add(org)
    db.commit()
    return dispatch_initial_full_sync(
        db,
        org_id=int(org.id),
        intent=InitialSyncIntent(run_id=str(marker["run_id"]), should_dispatch=True),
    )


def dispatch_initial_full_sync(
    db: Session,
    *,
    org_id: int,
    intent: InitialSyncIntent,
) -> dict:
    """Claim and dispatch a prepared run, returning its public status signal.

    Queue failures never roll back the successful ATS connection.  Instead the
    durable marker is changed to ``retry_pending`` for the beat recovery sweep.
    """
    if not intent.should_dispatch:
        db.expire_all()
        org = db.query(Organization).filter(Organization.id == org_id).first()
        return initial_sync_signal(org, intent.run_id)

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        return initial_sync_signal(None, intent.run_id)
    progress = org.bullhorn_sync_progress
    if not _same_queued_run(progress, intent.run_id):
        return initial_sync_signal(org, intent.run_id)
    attempts = int(progress.get("dispatch_attempts") or 0)

    dispatched_at = _now()
    claimed = dict(progress)
    claimed.update(
        {
            "dispatch_status": "dispatching",
            "dispatch_attempts": attempts + 1,
            "last_dispatched_at": dispatched_at.isoformat(),
            "recover_after": (dispatched_at + _DISPATCH_LEASE).isoformat(),
        }
    )
    org.bullhorn_sync_progress = claimed
    db.add(org)
    db.commit()

    try:
        _enqueue_initial_full_sync(
            org_id=org_id,
            run_id=intent.run_id,
            mode=str(progress.get("mode") or "full"),
            trigger=str(progress.get("trigger") or CONNECT_BOOTSTRAP_TRIGGER),
        )
    except Exception as exc:  # noqa: BLE001 - broker errors are recoverable
        # Never render the exception: broker URLs can contain credentials.
        logger.error(
            "Bullhorn initial sync dispatch deferred org_id=%s run_id=%s error_type=%s",
            org_id,
            intent.run_id,
            type(exc).__name__,
        )
        db.rollback()
        db.expire_all()
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org is not None and _same_queued_run(
            org.bullhorn_sync_progress, intent.run_id
        ):
            retry = dict(org.bullhorn_sync_progress)
            retry.update(
                {
                    "dispatch_status": "retry_pending",
                    "recover_after": _now().isoformat(),
                    "last_dispatch_error": "queue_unavailable",
                }
            )
            org.bullhorn_sync_progress = retry
            db.add(org)
            db.commit()

    db.expire_all()
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return initial_sync_signal(org, intent.run_id)


def recover_due_initial_syncs(*, limit: int = 100) -> dict:
    """Re-dispatch due connect bootstraps from their durable outbox markers."""
    scan_db = SessionLocal()
    try:
        orgs = (
            scan_db.query(Organization)
            .filter(Organization.bullhorn_connected.is_(True))
            .all()
        )
        now = _now()
        due = []
        dirty = False
        for org in orgs:
            pending = _pending_bootstrap(org)
            progress = org.bullhorn_sync_progress
            if pending is not None:
                if (
                    isinstance(progress, dict)
                    and progress.get("phase")
                    not in {"completed", "failed", "cancelled"}
                    and _progress_is_fresh(progress, now=now)
                ):
                    # The unrelated run still owns the Bullhorn mutex. Leave its
                    # progress untouched; this pending FULL intent survives in
                    # config and will be promoted on a later sweep.
                    continue
                if _pending_satisfied_by_full(org, progress, pending):
                    _mark_pending_satisfied(org, pending, now=now)
                    scan_db.add(org)
                    dirty = True
                    continue
                progress = {
                    **pending,
                    "phase": "queued",
                    "mode": "full",
                    "trigger": CONNECT_BOOTSTRAP_TRIGGER,
                    "dispatch_status": "retry_pending",
                    "recover_after": now.isoformat(),
                    "cancel_requested": False,
                }
                org.bullhorn_sync_progress = progress
                _clear_pending_bootstrap(org)
                scan_db.add(org)
                dirty = True
            if not isinstance(progress, dict):
                continue
            if progress.get("trigger") not in DURABLE_FULL_SYNC_TRIGGERS:
                continue
            phase = progress.get("phase")
            if phase != "queued":
                if phase in {"completed", "failed", "cancelled"}:
                    continue
                if _progress_is_fresh(progress, now=now):
                    continue
                progress = {
                    **progress,
                    "phase": "queued",
                    "dispatch_status": "retry_pending",
                    "recover_after": now.isoformat(),
                    "last_run_error": "stale_worker",
                    "cancel_requested": False,
                }
                org.bullhorn_sync_progress = progress
                scan_db.add(org)
                dirty = True
            recover_after = _as_utc(progress.get("recover_after"))
            if recover_after is None or recover_after <= now:
                run_id = str(progress.get("run_id") or "").strip()
                if run_id and len(due) < limit:
                    due.append((int(org.id), run_id))
        if dirty:
            scan_db.commit()
    finally:
        scan_db.close()

    dispatched = 0
    deferred = 0
    failed = 0
    for org_id, run_id in due:
        run_db = SessionLocal()
        try:
            signal = dispatch_initial_full_sync(
                run_db,
                org_id=org_id,
                intent=InitialSyncIntent(run_id=run_id, should_dispatch=True),
            )
            if signal.get("status") == "retry_pending":
                deferred += 1
            elif signal.get("status") in {"failed", "cancelled"}:
                failed += 1
            else:
                dispatched += 1
        except Exception as exc:  # pragma: no cover - isolate one tenant's recovery
            failed += 1
            logger.error(
                "Bullhorn initial sync recovery failed org_id=%s run_id=%s error_type=%s",
                org_id,
                run_id,
                type(exc).__name__,
            )
        finally:
            run_db.close()
    return {
        "status": "ok",
        "due": len(due),
        "dispatched": dispatched,
        "deferred": deferred,
        "failed": failed,
    }


def retry_marker_after_run_failure(
    progress: dict,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Return a bounded retry marker for a failed connect bootstrap run."""
    if progress.get("trigger") not in DURABLE_FULL_SYNC_TRIGGERS:
        return None
    attempts = int(progress.get("run_attempts") or 0)
    retry_at = now or _now()
    # Execution failures back off; broker-dispatch failures are picked up on the
    # next beat because dispatch_initial_full_sync stamps an immediately-due time.
    delay = timedelta(minutes=min(15, 2 ** min(4, max(0, attempts - 1))))
    marker = dict(progress)
    marker.update(
        {
            "phase": "queued",
            "dispatch_status": "retry_pending",
            "recover_after": (retry_at + delay).isoformat(),
            "last_run_error": "sync_failed",
            "cancel_requested": False,
        }
    )
    return marker


def initial_sync_signal(org: Organization | None, run_id: str) -> dict:
    """Credential-free tracked status returned by POST /connect."""
    progress = org.bullhorn_sync_progress if org is not None else None
    summary = org.bullhorn_last_sync_summary if org is not None else None
    record: dict = {}
    if isinstance(progress, dict) and str(progress.get("run_id") or "") == run_id:
        record = progress
    elif isinstance(summary, dict) and str(summary.get("run_id") or "") == run_id:
        record = summary
    elif org is not None:
        pending = _pending_bootstrap(org)
        if pending is not None and str(pending.get("run_id") or "") == run_id:
            record = pending
        else:
            config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
            if str(config.get("initial_full_sync_run_id") or "") == run_id:
                terminal = str(config.get("initial_full_sync_status") or "failed")
                record = {
                    "run_id": run_id,
                    "mode": "full",
                    "trigger": CONNECT_BOOTSTRAP_TRIGGER,
                    "phase": "completed" if terminal == "success" else terminal,
                    "status": terminal,
                }

    phase = str(record.get("phase") or "queued")
    if record.get("status"):
        status = str(record["status"])
    elif phase in {"queued", "waiting_for_active_sync"}:
        status = (
            "retry_pending"
            if record.get("dispatch_status") == "retry_pending"
            else "queued"
        )
    elif phase == "completed":
        status = "success"
    elif phase in {"failed", "cancelled"}:
        status = phase
    else:
        status = "running"
    return {
        "run_id": run_id,
        "mode": str(record.get("mode") or "full"),
        "trigger": str(record.get("trigger") or CONNECT_BOOTSTRAP_TRIGGER),
        "status": status,
        "phase": phase,
        "dispatch_attempts": int(record.get("dispatch_attempts") or 0),
        "run_attempts": int(record.get("run_attempts") or 0),
        "status_path": "/api/v1/bullhorn/sync/status",
    }


def initial_sync_status(org: Organization) -> dict | None:
    """Latest tracked bootstrap signal for status polling."""
    pending = _pending_bootstrap(org)
    if pending is not None:
        return initial_sync_signal(org, str(pending["run_id"]))
    progress = org.bullhorn_sync_progress
    if (
        isinstance(progress, dict)
        and progress.get("trigger") == CONNECT_BOOTSTRAP_TRIGGER
        and progress.get("run_id")
    ):
        return initial_sync_signal(org, str(progress["run_id"]))
    summary = org.bullhorn_last_sync_summary
    if (
        isinstance(summary, dict)
        and summary.get("trigger") == CONNECT_BOOTSTRAP_TRIGGER
        and summary.get("run_id")
    ):
        return initial_sync_signal(org, str(summary["run_id"]))
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    completed_id = str(config.get("initial_full_sync_run_id") or "").strip()
    return initial_sync_signal(org, completed_id) if completed_id else None


def _same_queued_run(progress: object, run_id: str) -> bool:
    return bool(
        isinstance(progress, dict)
        and progress.get("phase") == "queued"
        and str(progress.get("run_id") or "") == run_id
    )


def _progress_is_fresh(
    progress: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether a nonterminal runner has checkpointed inside its safety lease."""
    reference = now or _now()
    for key in ("updated_at", "started_at", "last_dispatched_at", "queued_at"):
        stamp = _as_utc(progress.get(key))
        if stamp is not None:
            return stamp >= reference - _LIVE_PROGRESS_LEASE
    return False


def _pending_satisfied_by_full(
    org: Organization,
    progress: object,
    pending: dict,
) -> bool:
    if (
        isinstance(progress, dict)
        and progress.get("phase") == "completed"
        and str(progress.get("mode") or "").strip().lower() == "full"
    ):
        return True
    summary = (
        org.bullhorn_last_sync_summary
        if isinstance(org.bullhorn_last_sync_summary, dict)
        else {}
    )
    if summary.get("status") != "success":
        return False
    if str(summary.get("mode") or "").strip().lower() != "full":
        return False
    completed_at = org.bullhorn_last_sync_at
    queued_at = _as_utc(pending.get("queued_at"))
    if completed_at is None or queued_at is None:
        return False
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return completed_at.astimezone(timezone.utc) >= queued_at


def _mark_pending_satisfied(
    org: Organization,
    pending: dict,
    *,
    now: datetime,
) -> None:
    _clear_pending_bootstrap(org)
    config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
    config.pop("initial_sync_bootstrap", None)
    config.update(
        {
            "initial_full_sync_run_id": str(pending.get("run_id") or ""),
            "initial_full_sync_status": "success",
            "initial_full_sync_finished_at": now.isoformat(),
        }
    )
    org.bullhorn_config = config


def _enqueue_initial_full_sync(
    *,
    org_id: int,
    run_id: str,
    mode: str,
    trigger: str,
) -> None:
    """Celery import seam kept lazy to avoid task-registration cycles."""
    from ....tasks.bullhorn_tasks import run_bullhorn_sync_run_task

    run_bullhorn_sync_run_task.delay(
        org_id=org_id,
        mode=mode,
        run_id=run_id,
        trigger=trigger,
    )
