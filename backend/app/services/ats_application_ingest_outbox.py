"""Transactional outbox for Workable/Bullhorn application-created work.

The ATS sync owns the outer transaction.  Producers persist one event beside
the new application and install a post-commit broker kick; the periodic sweep
is the recovery rail when that kick is lost.  The drain re-reads application
and role state, so an intervening Pause/Turn off prevents new paid work while
the cheap deterministic auto-reject still runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, or_
from sqlalchemy.orm import Session, object_session

from ..models.application_created_outbox import (
    APPLICATION_CREATED_COMPLETE,
    APPLICATION_CREATED_DISPATCHING,
    APPLICATION_CREATED_PENDING,
    ApplicationCreatedOutbox,
)
from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import CvScoreJob


logger = logging.getLogger("taali.ats_application_ingest_outbox")

_SESSION_PAYLOADS_KEY = "ats_application_created_outbox_ids"
_SESSION_HOOK_KEY = "ats_application_created_outbox_hook_installed"
_CLAIM_STALE_AFTER = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exception_type(exc: Exception) -> str:
    """Return non-sensitive exception metadata safe for receipts and logs."""

    return type(exc).__name__[:128]


def _exception_receipt(error_code: str, exc: Exception) -> str:
    return f"{error_code}:{_exception_type(exc)}"


def _install_after_commit_dispatch(session: Session) -> None:
    """Install one outer-transaction broker hook for this sync session."""

    if session.info.get(_SESSION_HOOK_KEY):
        return
    session.info[_SESSION_HOOK_KEY] = True

    @event.listens_for(session, "after_commit")
    def _dispatch_after_outer_commit(committed_session: Session) -> None:
        # Releasing a SAVEPOINT also emits after_commit.  The application is
        # externally visible only after the root transaction commits.
        if committed_session.in_nested_transaction():
            return
        payloads = committed_session.info.pop(_SESSION_PAYLOADS_KEY, {})
        for outbox_id in list(payloads):
            try:
                from ..tasks.application_ingest_tasks import (
                    dispatch_application_created_outbox,
                )

                dispatch_application_created_outbox.delay(int(outbox_id))
            except Exception as exc:
                # The row is already committed and remains pending.  Beat will
                # kick it again without requiring a fresh ATS sync or a human.
                logger.error(
                    "application-created post-commit kick failed "
                    "outbox_id=%s error_code=%s error_type=%s",
                    outbox_id,
                    "queue_unavailable",
                    _exception_type(exc),
                )

    @event.listens_for(session, "after_soft_rollback")
    def _discard_rolled_back_payloads(
        rolled_back_session: Session, previous_transaction
    ) -> None:
        payloads = rolled_back_session.info.get(_SESSION_PAYLOADS_KEY, {})
        if not payloads:
            return
        if getattr(previous_transaction, "parent", None) is None:
            rolled_back_session.info.pop(_SESSION_PAYLOADS_KEY, None)
            return
        for outbox_id, payload in list(payloads.items()):
            if payload.get("transaction") is previous_transaction:
                payloads.pop(outbox_id, None)


def enqueue_ats_application_created(
    application: CandidateApplication,
    *,
    score: bool = False,
    allow_paid_work: bool = True,
    requires_active_agent: bool = True,
    parse_origin: str | None = None,
) -> ApplicationCreatedOutbox:
    """Persist an idempotent ATS-ingest intent in the caller's transaction."""

    session = object_session(application)
    if session is None:
        raise RuntimeError("ATS application must be attached to a transaction")
    application_id = int(getattr(application, "id", 0) or 0)
    organization_id = int(getattr(application, "organization_id", 0) or 0)
    if application_id <= 0 or organization_id <= 0:
        raise RuntimeError("ATS application must be flushed before enqueue")

    related_role_pending = False
    if allow_paid_work:
        from .ats_related_role_dispatch import related_role_work_pending

        related_role_pending = related_role_work_pending(session, application)

    row = (
        session.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == application_id)
        .one_or_none()
    )
    if row is None:
        row = ApplicationCreatedOutbox(
            organization_id=organization_id,
            application_id=application_id,
            source=str(getattr(application, "source", None) or "ats")[:32],
            score_requested=bool(score),
            paid_work_requested=bool(allow_paid_work),
            requires_active_agent=bool(requires_active_agent),
            parse_origin=(str(parse_origin).strip()[:32] if parse_origin else None),
            status=APPLICATION_CREATED_PENDING,
        )
        session.add(row)
        session.flush()
    elif row.status == APPLICATION_CREATED_COMPLETE:
        # Full ATS syncs also revisit existing applications. Preserve the old
        # resume behavior without re-scoring every re-sync: a row first handled
        # while Pause/Turn off blocked paid work may reopen only when fresh
        # authority now exists and there is unfinished work to hand off.
        reopen = False
        if allow_paid_work and not row.paid_work_requested:
            row.paid_work_requested = True
        if score and not row.score_requested:
            row.score_requested = True
            row.score_dispatch_status = None
            reopen = True
        has_unparsed_cv = bool(
            (getattr(application, "cv_text", None) or "").strip()
            and getattr(application, "cv_sections", None) is None
        )
        if allow_paid_work and has_unparsed_cv and row.cv_parse_dispatch_status in {
            "not_requested",
            "authority_blocked",
            "no_cv_text",
        }:
            row.cv_parse_dispatch_status = None
            reopen = True
        if (
            allow_paid_work
            and row.score_requested
            and row.score_dispatch_status in {"authority_blocked", "admission_deferred"}
        ):
            row.score_dispatch_status = None
            reopen = True
        if related_role_pending:
            # SisterRoleEvaluation rows are their own idempotent receipts. A
            # missing/changed/pending row means the post-commit fan-out still
            # has work, even when parse/primary scoring already completed.
            reopen = True
        if reopen:
            row.status = APPLICATION_CREATED_PENDING
            row.completed_at = None
            row.claimed_at = None
            row.last_error = None
            session.flush()

    # Re-register a still-pending legacy/lost-kick row on the next sync commit.
    # Completed rows remain one-shot and are never re-fired by re-sync.
    if row.status != APPLICATION_CREATED_COMPLETE:
        payloads = session.info.setdefault(_SESSION_PAYLOADS_KEY, {})
        owner_transaction = session.get_nested_transaction() or session.get_transaction()
        payloads[int(row.id)] = {"transaction": owner_transaction}
        _install_after_commit_dispatch(session)
    return row


def _claim(db: Session, outbox_id: int) -> ApplicationCreatedOutbox | None:
    """Lease one pending/stale row with a compare-and-update claim."""

    now = _now()
    stale_before = now - _CLAIM_STALE_AFTER
    updated = (
        db.query(ApplicationCreatedOutbox)
        .filter(
            ApplicationCreatedOutbox.id == int(outbox_id),
            or_(
                ApplicationCreatedOutbox.status == APPLICATION_CREATED_PENDING,
                (
                    (ApplicationCreatedOutbox.status == APPLICATION_CREATED_DISPATCHING)
                    & (
                        (ApplicationCreatedOutbox.claimed_at.is_(None))
                        | (ApplicationCreatedOutbox.claimed_at < stale_before)
                    )
                ),
            ),
        )
        .update(
            {
                "status": APPLICATION_CREATED_DISPATCHING,
                "claimed_at": now,
                "attempts": ApplicationCreatedOutbox.attempts + 1,
                "last_error": None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated != 1:
        return None
    return db.get(ApplicationCreatedOutbox, int(outbox_id))


def _retry(
    db: Session,
    outbox_id: int,
    exc: Exception,
    *,
    error_code: str = "dispatch_failed",
) -> dict:
    error_type = _exception_type(exc)
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    if row is not None:
        row.status = APPLICATION_CREATED_PENDING
        row.claimed_at = None
        row.last_error = _exception_receipt(error_code, exc)
        db.commit()
    return {
        "status": "retry",
        "outbox_id": int(outbox_id),
        "error_code": error_code,
        "error_type": error_type,
    }


def _complete(
    db: Session,
    row: ApplicationCreatedOutbox,
    *,
    reason: str | None = None,
) -> dict:
    row.status = APPLICATION_CREATED_COMPLETE
    row.claimed_at = None
    row.completed_at = _now()
    if reason:
        row.last_error = reason[:2000]
    db.commit()
    return {
        "status": "complete",
        "outbox_id": int(row.id),
        "application_id": int(row.application_id),
        "reason": reason,
        "cv_parse": row.cv_parse_dispatch_status,
        "score": row.score_dispatch_status,
        "score_job_id": row.score_job_id,
    }


def dispatch_one(db: Session, *, outbox_id: int) -> dict:
    """Dispatch one committed intent, with fresh live execution authority."""

    row = _claim(db, int(outbox_id))
    if row is None:
        existing = db.get(ApplicationCreatedOutbox, int(outbox_id))
        return {
            "status": "already_handled" if existing is not None else "missing",
            "outbox_id": int(outbox_id),
        }

    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(row.application_id),
            CandidateApplication.organization_id == int(row.organization_id),
        )
        .one_or_none()
    )
    if app is None or app.deleted_at is not None:
        return _complete(db, row, reason="application is unavailable")

    # Cheap deterministic work is intentionally independent of paid-agent
    # authority.  Its worker is idempotent, so a crash in the tiny publish →
    # receipt-commit window can at worst create a harmless duplicate delivery.
    if row.auto_reject_dispatched_at is None:
        try:
            from ..tasks.automation_tasks import run_application_auto_reject

            run_application_auto_reject.delay(int(app.id))
            row = db.get(ApplicationCreatedOutbox, int(outbox_id))
            row.auto_reject_dispatched_at = _now()
            db.commit()
        except Exception as exc:
            db.rollback()
            return _retry(db, int(outbox_id), exc)

    # The cheap task can commit a decision in eager/tests or another worker can
    # Pause the role while this task waits.  Expire and re-read before *every*
    # paid dispatch decision.
    db.expire_all()
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    app = db.get(CandidateApplication, int(row.application_id))
    role = getattr(app, "role", None) if app is not None else None
    from ..domains.assessments_runtime.role_support import is_resolved
    from .job_page_lifecycle import role_allows_new_paid_ats_work

    live_paid_authority = bool(
        row.paid_work_requested
        and app is not None
        and app.deleted_at is None
        and not is_resolved(app)
        and (
            role_allows_new_paid_ats_work(role)
            if row.requires_active_agent
            else role is not None and getattr(role, "deleted_at", None) is None
        )
    )

    from .ats_cv_parse_outbox import dispatch_initial_cv_parse

    dispatch_initial_cv_parse(
        db,
        row=row,
        app=app,
        live_authority=live_paid_authority,
    )

    # Related-role evaluation rows and their broker kicks used to be created
    # inline by both ATS importers. Keep their cheap durable row creation on
    # this post-commit rail even if Pause/Off arrived after ingest; the scoring
    # worker re-checks authority and holds that row without spending. Thus a
    # worker never races the source transaction and no later sync/manual retry
    # is needed when authority returns.
    db.expire_all()
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    app = db.get(CandidateApplication, int(row.application_id))
    role = getattr(app, "role", None) if app is not None else None
    related_role_requested = bool(
        row.paid_work_requested
        and app is not None
        and app.deleted_at is None
        and not is_resolved(app)
    )
    if related_role_requested:
        try:
            from .ats_related_role_dispatch import dispatch_related_role_work

            dispatch_related_role_work(db, app)
        except Exception as exc:
            db.rollback()
            return _retry(db, int(outbox_id), exc)

    db.expire_all()
    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    app = db.get(CandidateApplication, int(row.application_id))
    role = getattr(app, "role", None) if app is not None else None
    live_score_authority = bool(
        row.score_requested
        and row.paid_work_requested
        and app is not None
        and app.deleted_at is None
        and not is_resolved(app)
        and (
            role_allows_new_paid_ats_work(role)
            if row.requires_active_agent
            else role is not None and getattr(role, "deleted_at", None) is None
        )
    )
    if row.score_dispatch_status is None:
        if not row.score_requested or not row.paid_work_requested:
            row.score_dispatch_status = "not_requested"
            db.commit()
        elif not live_score_authority:
            row.score_dispatch_status = "authority_blocked"
            db.commit()
        else:
            # Any score-job row proves this one-shot creation intent already
            # crossed into the score job's own durable recovery state.  This
            # closes the crash window where the first job finishes before the
            # outbox worker can persist its receipt and a retry might re-score.
            existing_job = (
                db.query(CvScoreJob)
                .filter(CvScoreJob.application_id == int(app.id))
                .order_by(CvScoreJob.id.desc())
                .first()
            )
            if existing_job is not None:
                row.score_job_id = int(existing_job.id)
                row.score_dispatch_status = "existing_job"
                db.commit()
            else:
                try:
                    from .cv_score_orchestrator import enqueue_score

                    score_job = enqueue_score(
                        db,
                        app,
                        force=False,
                        requires_active_agent=bool(row.requires_active_agent),
                    )
                except Exception as exc:
                    db.rollback()
                    # enqueue_score persists broker-failed jobs so its own
                    # five-minute recovery sweep can redispatch them.  Treat
                    # that durable job as a successful outbox handoff.
                    existing_job = (
                        db.query(CvScoreJob)
                        .filter(CvScoreJob.application_id == int(row.application_id))
                        .order_by(CvScoreJob.id.desc())
                        .first()
                    )
                    if existing_job is None:
                        return _retry(db, int(outbox_id), exc)
                    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
                    row.score_job_id = int(existing_job.id)
                    row.score_dispatch_status = "durable_job_recovery"
                    row.last_error = _exception_receipt(
                        "score_queue_recovery", exc
                    )
                    db.commit()
                else:
                    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
                    if score_job is None:
                        row.score_dispatch_status = "admission_deferred"
                    else:
                        row.score_job_id = int(score_job.id)
                        row.score_dispatch_status = "enqueued"
                    db.commit()

    row = db.get(ApplicationCreatedOutbox, int(outbox_id))
    return _complete(db, row)


def recoverable_ids(db: Session, *, limit: int = 200) -> list[int]:
    """Return pending or stale-leased rows for the Beat recovery sweep."""

    stale_before = _now() - _CLAIM_STALE_AFTER
    rows = (
        db.query(ApplicationCreatedOutbox.id)
        .filter(
            or_(
                ApplicationCreatedOutbox.status == APPLICATION_CREATED_PENDING,
                (
                    (ApplicationCreatedOutbox.status == APPLICATION_CREATED_DISPATCHING)
                    & (
                        (ApplicationCreatedOutbox.claimed_at.is_(None))
                        | (ApplicationCreatedOutbox.claimed_at < stale_before)
                    )
                ),
            )
        )
        .order_by(ApplicationCreatedOutbox.created_at.asc(), ApplicationCreatedOutbox.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [int(row[0]) for row in rows]


__all__ = [
    "dispatch_one",
    "enqueue_ats_application_created",
    "recoverable_ids",
]
