"""Celery email tasks (canonical location)."""

import logging
import random
import time

from ...tasks.celery_app import celery_app
from ...platform.config import settings

logger = logging.getLogger(__name__)


# Delivery-lifecycle states that the Resend webhook may have already advanced
# an invite to. A late writeback from this task must never downgrade one of
# these (mirrors resend_webhook_service._STATUS_RANK / _FAILURE_STATUSES).
_ADVANCED_INVITE_STATUSES = {"delivered", "opened", "clicked", "bounced", "complained"}

# Writeback resilience. The producer transaction (the bulk-approve /
# send-assessment action that enqueued this task) may still be committing when
# the worker runs, so a not-yet-visible row is retried rather than dropped —
# this is the race behind the 2026-06-25 "COMPLETED invite, NULL email_id"
# rows. Transient DB contention (deadlock / lock timeout) is retried too.
_WRITEBACK_MAX_ATTEMPTS = 5
_WRITEBACK_BACKOFF_SECONDS = 0.4

# Celery-level retry backoff when an email send fails (rate-limit / transient).
_EMAIL_RETRY_BASE_SECONDS = 8
_EMAIL_RETRY_RATE_LIMIT_START_SECONDS = 20
_EMAIL_RETRY_MAX_SECONDS = 600


class _RowNotYetVisible(Exception):
    """Internal: the assessment row isn't committed/visible yet — retry."""


def _apply_invite_status(asmt, status: str) -> None:
    """Set invite_email_status without downgrading a more-advanced webhook
    state. We only ever write ``sent`` or ``failed`` here; both yield to a
    delivered/opened/bounced state that a racing webhook already recorded."""
    current = (asmt.invite_email_status or "").strip()
    if current in _ADVANCED_INVITE_STATUSES:
        return
    asmt.invite_email_status = status


def _persist_invite_email_state(
    assessment_id: int,
    *,
    email_id: str | None = None,
    status: str | None = None,
    log_extra: dict | None = None,
) -> bool:
    """Robustly persist invite delivery tracking onto an Assessment row.

    Returns True iff the write landed. Resilient by design: the row may not be
    visible yet (producer transaction still committing) and bulk processing can
    hit transient DB contention — both are retried with short backoff instead
    of being silently swallowed (the original bare-except dropped the
    ``invite_email_id`` writeback under exactly these conditions).

    ``email_id`` and ``status`` are applied independently; ``status`` never
    downgrades a more-advanced webhook state already on the row.
    """
    from sqlalchemy.exc import OperationalError

    from ...platform.database import SessionLocal
    from ...models.assessment import Assessment

    last_err: Exception | None = None
    for attempt in range(1, _WRITEBACK_MAX_ATTEMPTS + 1):
        try:
            db = SessionLocal()
            try:
                asmt = (
                    db.query(Assessment)
                    .filter(Assessment.id == int(assessment_id))
                    .first()
                )
                if asmt is None:
                    raise _RowNotYetVisible()
                if email_id:
                    asmt.invite_email_id = email_id
                if status:
                    _apply_invite_status(asmt, status)
                db.commit()
                return True
            finally:
                db.close()
        except (_RowNotYetVisible, OperationalError) as exc:
            last_err = exc
            if attempt >= _WRITEBACK_MAX_ATTEMPTS:
                break
            time.sleep(_WRITEBACK_BACKOFF_SECONDS * attempt + random.uniform(0, 0.1))
        except Exception as exc:  # logic/unexpected — don't spin on it
            last_err = exc
            break

    logger.warning(
        "could not persist invite delivery state for assessment_id=%s "
        "(email_id=%s status=%s) after %d attempts: %s",
        assessment_id, email_id, status, _WRITEBACK_MAX_ATTEMPTS, last_err,
        extra=log_extra or {},
    )
    return False


def _email_retry_countdown(
    retries: int, *, rate_limited: bool, retry_after: float | None = None
) -> int:
    """Seconds to wait before the next Celery retry of an email send.

    Honors a Retry-After hint when present; otherwise exponential backoff.
    Rate-limit failures start the curve higher (a whole bulk burst is being
    re-spaced) and jitter de-synchronizes the retries so they don't re-burst.
    """
    if retry_after is not None and retry_after > 0:
        base: float = retry_after
    else:
        start = _EMAIL_RETRY_RATE_LIMIT_START_SECONDS if rate_limited else _EMAIL_RETRY_BASE_SECONDS
        base = start * (2 ** max(0, retries))
    base = min(base, _EMAIL_RETRY_MAX_SECONDS)
    return int(min(base + random.uniform(0, base * 0.25), _EMAIL_RETRY_MAX_SECONDS))


@celery_app.task(
    bind=True,
    max_retries=5,
    default_retry_delay=60,
    # Throttle bulk invite bursts under Resend's ~2 req/s API limit. A Home
    # review-queue bulk-approve enqueues one task per candidate; without a rate
    # cap they drain as fast as the worker runs and several get 429'd. Rate
    # limits are per-worker — we run a single worker today (see celery_app.py).
    rate_limit="2/s",
)
def send_assessment_email(
    self,
    candidate_email: str,
    candidate_name: str,
    token: str,
    org_name: str,
    position: str,
    assessment_id: int | None = None,
    candidate_facing_brand: str | None = None,
    reply_to: str | None = None,
    request_id: str | None = None,
):
    """Send assessment invitation email to candidate.

    Failure handling (2026-06-25 incident): a send that fails — most often a
    Resend 429 during a bulk burst — is retried with backoff, and once the
    retry budget is spent the invite is marked ``invite_email_status='failed'``
    so the recruiter's invited-candidate tracker shows it didn't go out. It is
    never silently dropped.
    """
    from .email_client import EmailService

    log_extra = {"request_id": request_id or self.request.id}
    if not (settings.RESEND_API_KEY or "").strip():
        logger.info(f"RESEND_API_KEY not set — skipping assessment email to {candidate_email}", extra=log_extra)
        return {"success": False, "skipped": True}

    try:
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
        result = email_svc.send_assessment_invite(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            token=token,
            assessment_id=assessment_id,
            org_name=org_name,
            position=position,
            frontend_url=settings.FRONTEND_URL,
            candidate_facing_brand=candidate_facing_brand,
            reply_to=reply_to,
        )
    except Exception as exc:  # defensive — send_assessment_invite catches internally
        result = {"success": False, "error": str(exc), "retryable": True, "rate_limited": False}

    if result.get("success"):
        logger.info(f"Assessment email sent to {candidate_email}", extra=log_extra)
        # Persist the Resend message id + 'sent' status so the delivery webhook
        # can correlate delivered/opened/bounced events and the recruiter's
        # invite tracker reflects the send. Robust against the producer-commit
        # race and DB contention that previously dropped this writeback.
        if assessment_id:
            _persist_invite_email_state(
                int(assessment_id),
                email_id=(result.get("email_id") or None),
                status="sent",
                log_extra=log_extra,
            )
        return result

    # ---- send failed ----
    error = str(result.get("error") or "Email send failed")
    rate_limited = bool(result.get("rate_limited"))
    retryable = result.get("retryable", True)

    if retryable and self.request.retries < self.max_retries:
        countdown = _email_retry_countdown(
            self.request.retries,
            rate_limited=rate_limited,
            retry_after=result.get("retry_after"),
        )
        logger.warning(
            "Assessment email to %s failed (attempt %d/%d, rate_limited=%s) — retrying in %ds: %s",
            candidate_email, self.request.retries + 1, self.max_retries + 1, rate_limited, countdown, error,
            extra=log_extra,
        )
        raise self.retry(exc=Exception(error), countdown=countdown)

    # Retry budget spent (or a permanent error) — surface the failure to the
    # recruiter instead of swallowing it. invite_email_status='failed' is read
    # by role_support._invite_tracking_payload → the invited-candidate tracker.
    logger.error(
        "Assessment email to %s permanently failed after %d attempt(s): %s",
        candidate_email, self.request.retries + 1, error,
        extra=log_extra,
    )
    if assessment_id:
        _persist_invite_email_state(int(assessment_id), status="failed", log_extra=log_extra)
    return {"success": False, "failed": True, "email_id": "", "error": error}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_results_email(self, user_email: str, candidate_name: str, score: float, assessment_id: int):
    """Notify hiring manager that assessment is complete."""
    from .email_client import EmailService

    if not (settings.RESEND_API_KEY or "").strip():
        logger.info(f"RESEND_API_KEY not set — skipping results email to {user_email}")
        return {"success": False, "skipped": True}
    try:
        email_svc = EmailService(api_key=settings.RESEND_API_KEY, from_email=settings.EMAIL_FROM)
        result = email_svc.send_results_notification(
            user_email=user_email,
            candidate_name=candidate_name,
            score=score,
            assessment_id=assessment_id,
            frontend_url=settings.FRONTEND_URL,
        )
        if not result["success"]:
            raise Exception(result.get("error", "Email send failed"))
        logger.info(f"Results email sent to {user_email} for assessment {assessment_id}")
        return result
    except Exception as exc:
        logger.error(f"Failed to send results email: {exc}")
        raise self.retry(exc=exc)
