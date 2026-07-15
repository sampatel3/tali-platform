"""Durable result/retry rail for ATS-ingest CV parsing.

The application-created outbox owns delivery until a parse worker acknowledges
success or a deterministic terminal failure. Broker acceptance is not
treated as completion: stale queued/running attempts and transient provider
failures are recovered by Beat. A compare-and-update worker claim prevents
duplicate messages from starting duplicate paid calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.application_created_outbox import ApplicationCreatedOutbox
from ..models.candidate_application import CandidateApplication


CV_PARSE_ENQUEUED = "enqueued"
CV_PARSE_RUNNING = "running"
CV_PARSE_RETRY_WAIT = "retry_wait"
CV_PARSE_SUCCEEDED = "succeeded"
CV_PARSE_FAILED = "failed"
CV_PARSE_BATCH_PENDING = "batch_pending"
CV_PARSE_AUTHORITY_BLOCKED = "authority_blocked"
CV_PARSE_NO_TEXT = "no_cv_text"

_ENQUEUED_STALE_AFTER = timedelta(minutes=5)
_RUNNING_STALE_AFTER = timedelta(minutes=15)
_AUTHORITY_RECHECK_AFTER = timedelta(minutes=5)
_RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30))
_SLOW_RETRY_DELAY = timedelta(hours=6)
_FAILURE_CODE_PREFIXES = (
    "application_deleted application_unavailable claude_call_failed client_init_failed "
    "cv_parse_failed cv_text_unavailable deterministic_parse_failure empty_cv_text "
    "input_token_ceiling_exceeded no_sections parse_failed prompt_render_failed "
    "queue_unavailable result_commit_failed unknown_origin "
    "usage_admission_failed validation_failed"
).split()
_DETERMINISTIC_FAILURE_CODES = {
    "client_init_failed",
    "empty_cv_text",
    "input_token_ceiling_exceeded",
    "prompt_render_failed",
    "validation_failed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exception_type(exc: Exception) -> str:
    name = type(exc).__name__
    return name[:128] if name.replace("_", "").isalnum() else "Exception"


def _safe_error_code(error: str | None) -> str:
    normalized = (error or "").strip().lower()
    for prefix in _FAILURE_CODE_PREFIXES:
        if normalized.startswith(prefix):
            return prefix
    return "cv_parse_failed"


def _effective_cv_text(app: CandidateApplication | None) -> str:
    if app is None:
        return ""
    return (app.cv_text or "").strip() or (
        (app.candidate.cv_text or "").strip() if app.candidate is not None else ""
    )


def _row(
    db: Session, *, application_id: int, outbox_id: int | None = None
) -> ApplicationCreatedOutbox | None:
    query = db.query(ApplicationCreatedOutbox).filter(
        ApplicationCreatedOutbox.application_id == int(application_id)
    )
    if outbox_id is not None:
        query = query.filter(ApplicationCreatedOutbox.id == int(outbox_id))
    return query.one_or_none()


def resolve_cv_parse_outbox_id(
    db: Session, *, application_id: int, outbox_id: int | None = None
) -> int | None:
    row = _row(db, application_id=application_id, outbox_id=outbox_id)
    return int(row.id) if row is not None else None


def _mark_succeeded(row: ApplicationCreatedOutbox) -> None:
    row.cv_parse_dispatch_status = CV_PARSE_SUCCEEDED
    row.cv_parse_claimed_at = None
    row.cv_parse_next_attempt_at = None
    row.cv_parse_last_error = None


def _schedule_retry(
    row: ApplicationCreatedOutbox,
    *,
    error: str,
    error_type: str | None = None,
    terminal: bool = False,
) -> str:
    attempts = int(row.cv_parse_attempts or 0)
    if terminal:
        row.cv_parse_dispatch_status = CV_PARSE_FAILED
        row.cv_parse_next_attempt_at = None
    else:
        index = max(attempts - 1, 0)
        delay = _RETRY_DELAYS[index] if index < len(_RETRY_DELAYS) else _SLOW_RETRY_DELAY
        row.cv_parse_dispatch_status = CV_PARSE_RETRY_WAIT
        row.cv_parse_next_attempt_at = _now() + delay
    row.cv_parse_claimed_at = None
    error_code = _safe_error_code(error)
    row.cv_parse_last_error = (
        f"{error_code}:{error_type}" if error_type else error_code
    )
    return str(row.cv_parse_dispatch_status)


def _hold_for_authority(
    db: Session, row: ApplicationCreatedOutbox, *, reason: str
) -> dict:
    row.cv_parse_dispatch_status = CV_PARSE_AUTHORITY_BLOCKED
    row.cv_parse_claimed_at = None
    row.cv_parse_next_attempt_at = _now() + _AUTHORITY_RECHECK_AFTER
    row.cv_parse_last_error = "authority_blocked"
    db.commit()
    return {"status": CV_PARSE_AUTHORITY_BLOCKED, "outbox_id": int(row.id)}


def _hold_for_text(db: Session, row: ApplicationCreatedOutbox) -> dict:
    row.cv_parse_dispatch_status = CV_PARSE_NO_TEXT
    row.cv_parse_claimed_at = None
    row.cv_parse_next_attempt_at = _now() + _AUTHORITY_RECHECK_AFTER
    row.cv_parse_last_error = "cv_text_unavailable"
    db.commit()
    return {"status": CV_PARSE_NO_TEXT, "outbox_id": int(row.id)}


def _publish(
    db: Session,
    *,
    row: ApplicationCreatedOutbox,
    application_id: int,
    origin: str,
) -> dict:
    outbox_id = int(row.id)
    row.cv_parse_dispatch_status = CV_PARSE_ENQUEUED
    row.cv_parse_dispatched_at = _now()
    row.cv_parse_claimed_at = None
    row.cv_parse_next_attempt_at = None
    row.cv_parse_last_error = None
    db.commit()
    try:
        from ..tasks.automation_tasks import parse_application_cv_sections

        parse_application_cv_sections.apply_async(
            (int(application_id),),
            kwargs={"origin": origin, "outbox_id": outbox_id},
        )
    except Exception as exc:
        db.rollback()
        current = db.get(ApplicationCreatedOutbox, outbox_id)
        if current is not None and current.cv_parse_dispatch_status != CV_PARSE_SUCCEEDED:
            error_type = _exception_type(exc)
            status = _schedule_retry(
                current,
                error="queue_unavailable",
                error_type=error_type,
            )
            db.commit()
            return {
                "status": status,
                "outbox_id": outbox_id,
                "error_code": "queue_unavailable",
                "error_type": error_type,
            }
    db.expire_all()
    current = db.get(ApplicationCreatedOutbox, outbox_id)
    return {
        "status": (
            current.cv_parse_dispatch_status if current is not None else CV_PARSE_ENQUEUED
        ),
        "outbox_id": outbox_id,
    }


def dispatch_initial_cv_parse(
    db: Session,
    *,
    row: ApplicationCreatedOutbox,
    app: CandidateApplication,
    live_authority: bool,
) -> dict:
    if app.cv_sections is not None:
        _mark_succeeded(row)
        db.commit()
        return {"status": CV_PARSE_SUCCEEDED, "outbox_id": int(row.id)}
    if row.cv_parse_dispatch_status is not None:
        return {"status": str(row.cv_parse_dispatch_status), "outbox_id": int(row.id)}
    if not row.paid_work_requested:
        row.cv_parse_dispatch_status = "not_requested"
        db.commit()
        return {"status": "not_requested", "outbox_id": int(row.id)}
    if not live_authority:
        return _hold_for_authority(db, row, reason="Role is not authorized for paid ATS work")
    if not _effective_cv_text(app):
        return _hold_for_text(db, row)

    from ..cv_parsing.origins import normalize_cv_parse_origin
    from ..platform.config import settings

    origin = normalize_cv_parse_origin(row.parse_origin)
    if origin is None:
        row.cv_parse_dispatch_status = CV_PARSE_FAILED
        row.cv_parse_last_error = "unknown_origin"
        db.commit()
        return {"status": CV_PARSE_FAILED, "outbox_id": int(row.id)}
    if settings.CV_PARSE_BATCH_ENABLED:
        row.cv_parse_dispatch_status = CV_PARSE_BATCH_PENDING
        row.cv_parse_last_error = None
        db.commit()
        return {"status": CV_PARSE_BATCH_PENDING, "outbox_id": int(row.id)}
    return _publish(
        db,
        row=row,
        application_id=int(app.id),
        origin=origin,
    )


def claim_cv_parse_attempt(
    db: Session, *, application_id: int, outbox_id: int
) -> dict:
    row = _row(db, application_id=application_id, outbox_id=outbox_id)
    if row is None:
        return {"claimed": False, "reason": "outbox_missing"}
    app = db.get(CandidateApplication, int(application_id))
    if app is not None and app.cv_sections is not None:
        _mark_succeeded(row)
        db.commit()
        return {"claimed": False, "reason": "already_parsed", "outbox_id": int(row.id)}
    now = _now()
    updated = (
        db.query(ApplicationCreatedOutbox)
        .filter(
            ApplicationCreatedOutbox.id == int(row.id),
            ApplicationCreatedOutbox.application_id == int(application_id),
            ApplicationCreatedOutbox.cv_parse_dispatch_status.in_(
                (CV_PARSE_ENQUEUED, CV_PARSE_BATCH_PENDING)
            ),
        )
        .update(
            {
                "cv_parse_dispatch_status": CV_PARSE_RUNNING,
                "cv_parse_attempts": ApplicationCreatedOutbox.cv_parse_attempts + 1,
                "cv_parse_claimed_at": now,
                "cv_parse_next_attempt_at": None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return {
        "claimed": updated == 1,
        "reason": "claimed" if updated == 1 else "already_claimed",
        "outbox_id": int(row.id),
    }


def record_cv_parse_success(db: Session, *, outbox_id: int) -> str:
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    if row is None:
        return "missing"
    _mark_succeeded(row)
    db.commit()
    return CV_PARSE_SUCCEEDED


def record_cv_parse_failure(
    db: Session,
    *,
    outbox_id: int,
    error: str,
    terminal: bool = False,
) -> str:
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    if row is None:
        return "missing"
    if row.cv_parse_dispatch_status == CV_PARSE_SUCCEEDED:
        return CV_PARSE_SUCCEEDED
    status = _schedule_retry(row, error=error, terminal=terminal)
    db.commit()
    return status


def record_cv_parse_authority_blocked(
    db: Session, *, outbox_id: int, reason: str
) -> str:
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    if row is None:
        return "missing"
    return str(_hold_for_authority(db, row, reason=reason)["status"])


def record_cv_parse_missing_text(db: Session, *, outbox_id: int) -> str:
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    if row is None:
        return "missing"
    return str(_hold_for_text(db, row)["status"])


def cached_failure_for_application(app: CandidateApplication) -> tuple[str, bool]:
    cv_text = _effective_cv_text(app)
    if not cv_text:
        return "CV text is not available", False
    from ..cv_parsing import MODEL_VERSION, PROMPT_VERSION
    from ..cv_parsing import cache as cache_module

    key = cache_module.compute_cache_key(
        cv_text=cv_text,
        prompt_version=PROMPT_VERSION,
        model_version=MODEL_VERSION,
    )
    parsed = cache_module.get(key)
    if parsed is not None and parsed.parse_failed:
        error_code = _safe_error_code(parsed.error_reason)
        return error_code, error_code in _DETERMINISTIC_FAILURE_CODES
    return "no_sections", False


def record_application_parse_success(db: Session, *, application_id: int) -> None:
    row = _row(db, application_id=application_id)
    if row is not None:
        _mark_succeeded(row)


def record_application_parse_failure(
    db: Session, *, application_id: int, error: str, terminal: bool
) -> None:
    row = _row(db, application_id=application_id)
    if row is not None:
        _schedule_retry(row, error=error, terminal=terminal)


def recoverable_cv_parse_ids(db: Session, *, limit: int = 200) -> list[int]:
    now = _now()
    # A worker may commit cv_sections and die before acknowledging the outbox.
    succeeded_rows = (
        db.query(ApplicationCreatedOutbox)
        .join(
            CandidateApplication,
            CandidateApplication.id == ApplicationCreatedOutbox.application_id,
        )
        .filter(
            CandidateApplication.cv_sections.isnot(None),
            ApplicationCreatedOutbox.cv_parse_dispatch_status.in_(
                (
                    CV_PARSE_ENQUEUED,
                    CV_PARSE_RUNNING,
                    CV_PARSE_RETRY_WAIT,
                    CV_PARSE_BATCH_PENDING,
                    CV_PARSE_AUTHORITY_BLOCKED,
                    CV_PARSE_NO_TEXT,
                )
            ),
        )
        .all()
    )
    for row in succeeded_rows:
        _mark_succeeded(row)

    due = (
        db.query(ApplicationCreatedOutbox)
        .filter(
            or_(
                (
                    ApplicationCreatedOutbox.cv_parse_dispatch_status == CV_PARSE_ENQUEUED
                )
                & (
                    ApplicationCreatedOutbox.cv_parse_dispatched_at.is_(None)
                    | (
                        ApplicationCreatedOutbox.cv_parse_dispatched_at
                        <= now - _ENQUEUED_STALE_AFTER
                    )
                ),
                (
                    ApplicationCreatedOutbox.cv_parse_dispatch_status == CV_PARSE_RUNNING
                )
                & (
                    ApplicationCreatedOutbox.cv_parse_claimed_at.is_(None)
                    | (
                        ApplicationCreatedOutbox.cv_parse_claimed_at
                        <= now - _RUNNING_STALE_AFTER
                    )
                ),
                (
                    ApplicationCreatedOutbox.cv_parse_dispatch_status.in_(
                        (CV_PARSE_RETRY_WAIT, CV_PARSE_AUTHORITY_BLOCKED, CV_PARSE_NO_TEXT)
                    )
                )
                & (
                    ApplicationCreatedOutbox.cv_parse_next_attempt_at.is_(None)
                    | (ApplicationCreatedOutbox.cv_parse_next_attempt_at <= now)
                ),
            )
        )
        .order_by(ApplicationCreatedOutbox.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    ids = [int(row.id) for row in due]
    db.commit()
    return ids


def redispatch_cv_parse(db: Session, *, outbox_id: int) -> dict:
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    if row is None:
        return {"status": "missing", "outbox_id": int(outbox_id)}
    app = db.get(CandidateApplication, int(row.application_id))
    if app is None or app.deleted_at is not None:
        _schedule_retry(row, error="Application is unavailable", terminal=True)
        db.commit()
        return {"status": CV_PARSE_FAILED, "outbox_id": int(row.id)}
    if app.cv_sections is not None:
        _mark_succeeded(row)
        db.commit()
        return {"status": CV_PARSE_SUCCEEDED, "outbox_id": int(row.id)}

    from ..domains.assessments_runtime.role_support import is_resolved
    from ..cv_parsing.origins import normalize_cv_parse_origin
    from ..platform.config import settings
    from .job_page_lifecycle import role_allows_new_paid_ats_work

    role = app.role
    authorized = bool(
        row.paid_work_requested
        and not is_resolved(app)
        and (
            role_allows_new_paid_ats_work(role, db=db)
            if row.requires_active_agent
            else role is not None and role.deleted_at is None
        )
    )
    if not authorized:
        return _hold_for_authority(db, row, reason="Role is not authorized for paid ATS work")
    if not _effective_cv_text(app):
        return _hold_for_text(db, row)
    origin = normalize_cv_parse_origin(row.parse_origin)
    if origin is None:
        _schedule_retry(row, error="Unknown CV parse origin", terminal=True)
        db.commit()
        return {"status": CV_PARSE_FAILED, "outbox_id": int(row.id)}
    if settings.CV_PARSE_BATCH_ENABLED:
        row.cv_parse_dispatch_status = CV_PARSE_BATCH_PENDING
        row.cv_parse_claimed_at = None
        row.cv_parse_next_attempt_at = None
        row.cv_parse_last_error = None
        db.commit()
        return {"status": CV_PARSE_BATCH_PENDING, "outbox_id": int(row.id)}
    return _publish(
        db,
        row=row,
        application_id=int(app.id),
        origin=origin,
    )


__all__ = [
    "cached_failure_for_application",
    "claim_cv_parse_attempt",
    "dispatch_initial_cv_parse",
    "record_application_parse_failure",
    "record_application_parse_success",
    "record_cv_parse_authority_blocked",
    "record_cv_parse_failure",
    "record_cv_parse_missing_text",
    "record_cv_parse_success",
    "recoverable_cv_parse_ids",
    "redispatch_cv_parse",
    "resolve_cv_parse_outbox_id",
]
