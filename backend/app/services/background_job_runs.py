"""Helpers for recording in-memory job kinds (scoring batch, CV fetch, graph
sync) into ``background_job_runs`` so the Settings → Background jobs panel
can render history beyond the current in-process state.

These helpers swallow exceptions: a failed bookkeeping write must never
break the actual job. The in-memory dict remains the source of truth for
live progress; the row is the source of truth for history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy.exc import IntegrityError

from ..models.background_job_run import (
    BackgroundJobRun,
    SCOPE_KIND_ORG,
    SCOPE_KIND_ROLE,
)
from .ats_job_run_errors import AtsJobRunDispatchConflict
from ..platform.database import SessionLocal


logger = logging.getLogger(__name__)

# Initial Celery delivery plus ``max_retries=12`` retries.
ATS_MAX_DELIVERY_ATTEMPTS = 13


def create_run(
    *,
    kind: str,
    scope_kind: str,
    scope_id: int,
    organization_id: int,
    counters: Mapping[str, Any] | None = None,
    status: str = "running",
    dispatch_key: str | None = None,
) -> int | None:
    """Insert a new background_job_runs row. Returns the new id, or None on failure."""
    stable_dispatch_key = str(dispatch_key or "").strip() or None
    if stable_dispatch_key is not None and len(stable_dispatch_key) > 200:
        logger.error("background_job_runs: dispatch key exceeds 200 characters")
        return None
    db = SessionLocal()
    try:
        row = BackgroundJobRun(
            kind=kind,
            scope_kind=scope_kind,
            scope_id=int(scope_id),
            organization_id=int(organization_id),
            status=status,
            counters=dict(counters or {}),
            dispatch_key=stable_dispatch_key,
        )
        db.add(row)
        # Capture the database-assigned primary key before commit. A refresh
        # after commit creates a false-negative window: the insert can be
        # durable while a follow-up SELECT fails, causing a strict ATS caller
        # to report 503 even though Beat can see and replay the committed row.
        db.flush()
        run_id = int(row.id)
        db.commit()
        return run_id
    except IntegrityError:
        # A unique dispatch key means another producer won the idempotency
        # race. The caller re-reads that row and must not publish a second task.
        db.rollback()
        if stable_dispatch_key:
            logger.info(
                "background_job_runs: duplicate dispatch collapsed key=%s",
                stable_dispatch_key,
            )
            return None
        logger.exception("background_job_runs: create integrity failure")
        return None
    except Exception:
        logger.exception("background_job_runs: create failed")
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        db.close()


def find_run_by_dispatch_key(
    dispatch_key: str | None,
    *,
    organization_id: int,
    kind: str,
    op_type: str,
    expected_counters: Mapping[str, Any] | None = None,
) -> int | None:
    """Find a matching producer receipt without accepting a borrowed key."""

    stable_dispatch_key = str(dispatch_key or "").strip()
    if not stable_dispatch_key:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.dispatch_key == stable_dispatch_key,
                BackgroundJobRun.organization_id == int(organization_id),
                BackgroundJobRun.kind == str(kind),
            )
            .one_or_none()
        )
        if row is None:
            return None
        stored_counters = dict(row.counters or {})
        if str(stored_counters.get("op_type") or "") != str(op_type):
            logger.error(
                "background_job_runs: dispatch receipt op mismatch id=%s",
                row.id,
            )
            if expected_counters:
                raise AtsJobRunDispatchConflict(op_type)
            return None
        for counter_name, expected_value in dict(expected_counters or {}).items():
            if str(stored_counters.get(counter_name) or "") != str(expected_value):
                logger.info(
                    "background_job_runs: dispatch receipt intent mismatch id=%s",
                    row.id,
                )
                raise AtsJobRunDispatchConflict(op_type)
        return int(row.id)
    except AtsJobRunDispatchConflict:
        raise
    except Exception:
        logger.exception("background_job_runs: dispatch receipt lookup failed")
        return None
    finally:
        db.close()


def update_run(
    run_id: int | None,
    *,
    status: str | None = None,
    counters: Mapping[str, Any] | None = None,
    error: str | None = None,
    finished: bool = False,
    cancel_requested: bool = False,
    pre_provider_failure: bool = False,
) -> bool:
    """Update an existing run row and report whether it committed."""
    if not run_id:
        return False
    db = SessionLocal()
    try:
        row = db.query(BackgroundJobRun).filter(BackgroundJobRun.id == run_id).first()
        if row is None:
            return False
        if status is not None:
            row.status = status
        if pre_provider_failure:
            safe_counters = dict(row.counters or {})
            safe_counters.update(
                provider_called=False,
                failure_phase="before_provider_claim",
            )
            row.counters = safe_counters
        elif counters is not None:
            replacement = dict(counters)
            prior_counters = dict(row.counters or {})
            for identity_key in (
                "note_body_sha256",
                "note_intent_sha256",
                "note_dispatch_sha256",
            ):
                prior_value = prior_counters.get(identity_key)
                if prior_value:
                    replacement[identity_key] = str(prior_value)
            row.counters = replacement
        if error is not None:
            row.error = error
        now = datetime.now(timezone.utc)
        if finished:
            row.finished_at = now
        if cancel_requested and row.cancel_requested_at is None:
            row.cancel_requested_at = now
        db.commit()
        return True
    except Exception:
        logger.exception("background_job_runs: update failed for id=%s", run_id)
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def merge_progress(run_id: int | None, progress: Mapping[str, Any]) -> bool:
    """Durably replace one run's public progress without losing recovery data.

    Long ATS batches commit after every application. Their encrypted replay
    payload and delivery lease live beside ``progress`` in ``counters``; using
    :func:`update_run` would replace those fields and make a worker crash
    unrecoverable. This locked merge keeps both rails intact.
    """

    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return False
        counters = dict(row.counters or {})
        counters["progress"] = dict(progress)
        row.counters = counters
        db.commit()
        return True
    except Exception as exc:
        logger.error(
            "background_job_runs: progress merge failed id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        db.rollback()
        return False
    finally:
        db.close()


def mark_dispatched(run_id: int | None) -> bool:
    """Atomically move a replayable op from dispatching to queued.

    A very fast worker may already have changed the row to ``running``; the
    conditional update intentionally leaves that newer state untouched.
    """
    if not run_id:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.status == "dispatching",
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return False
        counters = dict(row.counters or {})
        counters["last_dispatched_at"] = datetime.now(timezone.utc).isoformat()
        # Keep the status predicate on the write as well as the locked read.
        # PostgreSQL row locking preserves the worker's counter changes; the
        # compare-and-set protects other dialects and future writers that do
        # not participate in the same locking protocol.
        updated = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.status == "dispatching",
                BackgroundJobRun.finished_at.is_(None),
            )
            .update(
                {
                    BackgroundJobRun.counters: counters,
                    BackgroundJobRun.status: "queued",
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.commit()
            return False
        db.commit()
        return True
    except Exception as exc:
        logger.error(
            "background_job_runs: dispatch mark failed id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        db.rollback()
        return False
    finally:
        db.close()


def release_ats_run_for_retry(
    run_id: int | None,
    *,
    delay_seconds: int,
) -> bool:
    """Return a failed provider attempt to a durable, claimable wait state."""

    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.status == "running",
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        counters = dict(row.counters or {})
        counters["last_retry_scheduled_at"] = now.isoformat()
        counters["retry_not_before"] = (
            now + timedelta(seconds=max(0, int(delay_seconds)))
        ).isoformat()
        row.counters = counters
        row.status = "queued"
        db.commit()
        return True
    except Exception as exc:
        logger.error(
            "background_job_runs: ATS retry release failed id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        db.rollback()
        return False
    finally:
        db.close()


def claim_ats_run(
    run_id: int | None,
    *,
    organization_id: int,
    expected_kind: str,
    op_type: str,
) -> bool:
    """Claim a provider operation only when its durable receipt matches."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.organization_id == int(organization_id),
                BackgroundJobRun.kind == str(expected_kind),
                BackgroundJobRun.status.in_(("dispatching", "queued")),
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return False
        counters = dict(row.counters or {})
        if str(counters.get("op_type") or "") != str(op_type):
            logger.error(
                "background_job_runs: ATS claim op mismatch id=%s organization_id=%s",
                run_id,
                organization_id,
            )
            return False
        now = datetime.now(timezone.utc)
        attempts = int(counters.get("delivery_attempts") or 0)
        if attempts >= ATS_MAX_DELIVERY_ATTEMPTS:
            counters.update(
                code="delivery_attempts_exhausted",
                provider_called=False,
                delivery_attempts=attempts,
            )
            row.counters = counters
            row.status = "failed"
            row.error = "ATS delivery attempt limit exhausted"
            row.finished_at = now
            db.commit()
            return False
        retry_not_before = counters.get("retry_not_before")
        if retry_not_before:
            try:
                due = datetime.fromisoformat(
                    str(retry_not_before).replace("Z", "+00:00")
                )
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if due.astimezone(timezone.utc) > now:
                    return False
            except (TypeError, ValueError):
                logger.warning(
                    "background_job_runs: invalid ATS retry timestamp id=%s",
                    run_id,
                )
        counters.pop("retry_not_before", None)
        counters["last_started_at"] = now.isoformat()
        counters["delivery_attempts"] = attempts + 1
        row.counters = counters
        row.status = "running"
        db.commit()
        return True
    except Exception as exc:
        logger.error(
            "background_job_runs: ATS claim failed id=%s organization_id=%s error_type=%s",
            run_id,
            organization_id,
            type(exc).__name__,
        )
        db.rollback()
        return False
    finally:
        db.close()


def fail_claimable_ats_run_before_provider(
    run_id: int | None,
    *,
    organization_id: int,
    expected_kind: str,
    op_type: str,
    code: str,
    error: str,
) -> bool:
    """CAS-fail only the still-claimable delivery named by the broker message."""

    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.organization_id == int(organization_id),
                BackgroundJobRun.kind == str(expected_kind),
                BackgroundJobRun.status.in_(("dispatching", "queued")),
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return False
        counters = dict(row.counters or {})
        if str(counters.get("op_type") or "") != str(op_type):
            return False
        counters.update(
            code=str(code),
            provider_called=False,
            failure_phase="before_provider_claim",
        )
        row.counters = counters
        row.status = "failed"
        row.error = str(error)[:2000]
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


__all__ = [
    "create_run",
    "find_run_by_dispatch_key",
    "update_run",
    "merge_progress",
    "mark_dispatched",
    "claim_ats_run",
    "release_ats_run_for_retry",
    "SCOPE_KIND_ROLE",
    "SCOPE_KIND_ORG",
]
