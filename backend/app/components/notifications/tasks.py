"""Celery email tasks (canonical location)."""

import logging
import random
import time
from datetime import datetime, timedelta, timezone

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

# A Celery retry chain is intentionally finite so one provider incident cannot
# monopolise a worker forever.  Exhausting that short chain is *not* a terminal
# invite failure: the durable sweep takes over after this cooldown and starts a
# fresh bounded chain.  Stable Resend idempotency keys make the hand-off safe
# even if the provider accepted a request whose response was lost.
_EMAIL_RECOVERY_COOLDOWN_SECONDS = 900
_EMAIL_RECOVERY_LEASE_SECONDS = 900
_EMAIL_RECOVERY_RETRY_GRACE_SECONDS = 120

_UNSET = object()


class _RowNotYetVisible(Exception):
    """Internal: the assessment row isn't committed/visible yet — retry."""


def _apply_invite_status(asmt, status: str) -> None:
    """Set invite_email_status without downgrading a more-advanced webhook
    state. Provider-task/outbox states all yield to real
    delivered/opened/bounced evidence that a racing webhook already recorded."""
    current = (asmt.invite_email_status or "").strip()
    if current in _ADVANCED_INVITE_STATUSES:
        return
    asmt.invite_email_status = status


def _persist_invite_email_state(
    assessment_id: int,
    *,
    email_id: str | None = None,
    status: str | None = None,
    retry_count=_UNSET,
    next_attempt_at=_UNSET,
    claimed_at=_UNSET,
    last_error=_UNSET,
    expected_generation=_UNSET,
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
                if expected_generation is not _UNSET and int(
                    asmt.invite_email_send_generation or 0
                ) != int(expected_generation):
                    # A recruiter/agent explicitly requested a newer resend
                    # while this older provider attempt was in flight.  Never
                    # let the stale result clobber the new outbox intent.
                    return False
                if email_id:
                    asmt.invite_email_id = email_id
                if status:
                    _apply_invite_status(asmt, status)
                if retry_count is not _UNSET:
                    asmt.invite_email_retry_count = max(0, int(retry_count or 0))
                if next_attempt_at is not _UNSET:
                    asmt.invite_email_next_attempt_at = next_attempt_at
                if claimed_at is not _UNSET:
                    asmt.invite_email_claimed_at = claimed_at
                if last_error is not _UNSET:
                    asmt.invite_email_last_error = (
                        str(last_error)[:4000] if last_error else None
                    )
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


def _confirm_invite_provider_success(
    assessment_id: int,
    *,
    email_id: str,
    expected_generation: int,
    log_extra: dict | None = None,
) -> dict:
    """Robustly commit provider truth + local pipeline in one transaction."""
    from sqlalchemy.exc import OperationalError

    from ...platform.database import SessionLocal
    from ...services.assessment_invite_delivery import (
        confirm_assessment_invite_provider_success,
    )

    last_err: Exception | None = None
    for attempt in range(1, _WRITEBACK_MAX_ATTEMPTS + 1):
        db = SessionLocal()
        try:
            result = confirm_assessment_invite_provider_success(
                db,
                assessment_id=int(assessment_id),
                email_id=str(email_id),
                expected_generation=int(expected_generation),
            )
            if result.get("reason") == "missing":
                raise _RowNotYetVisible()
            return result
        except (_RowNotYetVisible, OperationalError) as exc:
            db.rollback()
            last_err = exc
            if attempt < _WRITEBACK_MAX_ATTEMPTS:
                time.sleep(
                    _WRITEBACK_BACKOFF_SECONDS * attempt + random.uniform(0, 0.1)
                )
        except Exception as exc:
            db.rollback()
            last_err = exc
            break
        finally:
            db.close()
    logger.warning(
        "could not confirm assessment invite provider success assessment_id=%s "
        "generation=%s after %d attempts: %s",
        assessment_id,
        expected_generation,
        _WRITEBACK_MAX_ATTEMPTS,
        last_err,
        extra=log_extra or {},
    )
    return {"confirmed": False, "reason": "writeback_failed", "error": str(last_err or "")}


def _aware(value):
    """Normalise SQLite-naive and production-aware DB timestamps."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _claim_invite_email_send(
    assessment_id: int,
    *,
    recovery_dispatch: bool,
    requested_idempotency_key: str | None,
) -> dict:
    """Claim one provider-send attempt and collapse duplicate task delivery."""
    from ...domains.integrations_notifications.invite_flow import (
        INVITE_RETRYING,
        assessment_invite_idempotency_key,
    )
    from ...models.assessment import Assessment
    from ...platform.database import SessionLocal

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        row = (
            db.query(Assessment)
            .filter(Assessment.id == int(assessment_id))
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise _RowNotYetVisible()
        current = str(row.invite_email_status or "").strip()
        generation = int(row.invite_email_send_generation or 0)
        expected_key = assessment_invite_idempotency_key(row)
        if requested_idempotency_key and requested_idempotency_key != expected_key:
            return {
                "claimed": False,
                "reason": "superseded_generation",
                "status": current,
                "generation": generation,
                "idempotency_key": expected_key,
            }
        if bool(getattr(row, "is_voided", False)):
            return {"claimed": False, "reason": "voided", "status": current}
        if current in (_ADVANCED_INVITE_STATUSES | {"sent", "failed", "dispatch_failed"}):
            return {"claimed": False, "reason": "terminal", "status": current}

        # A fresh retrying row is either an active provider call or a future
        # Celery retry.  Only the explicit recovery sweep may override that
        # lease after applying its own stale/due checks.
        if current == INVITE_RETRYING and not recovery_dispatch:
            next_at = _aware(row.invite_email_next_attempt_at)
            claimed_at = _aware(row.invite_email_claimed_at)
            if next_at is not None and next_at > now:
                return {
                    "claimed": False,
                    "reason": "retry_scheduled",
                    "status": current,
                }
            if next_at is None and claimed_at is not None and claimed_at > (
                now - timedelta(seconds=_EMAIL_RECOVERY_LEASE_SECONDS)
            ):
                return {
                    "claimed": False,
                    "reason": "in_flight",
                    "status": current,
                }

        row.invite_email_status = INVITE_RETRYING
        row.invite_email_claimed_at = now
        row.invite_email_next_attempt_at = None
        db.commit()
        return {
            "claimed": True,
            "status": INVITE_RETRYING,
            "generation": generation,
            "idempotency_key": expected_key,
        }
    finally:
        db.close()


def _permanent_provider_4xx(result: dict) -> bool:
    """Only an explicit non-rate-limit provider 4xx requires HITL."""
    try:
        code = int(str(result.get("error_code") or "").strip())
    except (TypeError, ValueError):
        return False
    return 400 <= code < 500 and code != 429


def _invalidate_resend_probe(error: str) -> None:
    """Make the next default-worker heartbeat re-probe provider delivery."""
    try:
        from ...services.agent_worker_health import invalidate_resend_probe_cache

        invalidate_resend_probe_cache(error=error)
    except Exception:
        # Retry durability lives in Postgres; probe-cache invalidation is only
        # an optimisation and must never mask the underlying delivery result.
        logger.warning("could not invalidate Resend readiness probe", exc_info=True)


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
    idempotency_key: str | None = None,
    recovery_dispatch: bool = False,
):
    """Send assessment invitation email to candidate.

    Transient failures first use bounded Celery retries, then move to durable
    ``retry_wait`` for the Beat recovery loop.  Only an explicit permanent
    provider 4xx becomes ``failed`` and requires human intervention.
    """
    from .email_client import EmailService

    log_extra = {"request_id": request_id or self.request.id}
    requested_idempotency_key = str(idempotency_key or "").strip() or None
    stable_idempotency_key = requested_idempotency_key
    send_generation = None

    if assessment_id:
        try:
            claim = _claim_invite_email_send(
                int(assessment_id),
                recovery_dispatch=bool(recovery_dispatch),
                requested_idempotency_key=requested_idempotency_key,
            )
        except _RowNotYetVisible as exc:
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc, countdown=_EMAIL_RETRY_BASE_SECONDS)
            return {
                "success": False,
                "retry_wait": True,
                "error": "assessment row was not visible before retry exhaustion",
            }
        if not claim.get("claimed"):
            return {
                "success": claim.get("status") in (_ADVANCED_INVITE_STATUSES | {"sent"}),
                "deduplicated": True,
                "status": claim.get("status"),
                "reason": claim.get("reason"),
            }
        stable_idempotency_key = str(claim["idempotency_key"])
        send_generation = int(claim["generation"])

    if not (settings.RESEND_API_KEY or "").strip():
        error = "RESEND_API_KEY is not configured"
        logger.error("%s — assessment email to %s deferred", error, candidate_email, extra=log_extra)
        if assessment_id:
            _persist_invite_email_state(
                int(assessment_id),
                status="retry_wait",
                next_attempt_at=datetime.now(timezone.utc)
                + timedelta(seconds=_EMAIL_RECOVERY_COOLDOWN_SECONDS),
                claimed_at=None,
                last_error=error,
                expected_generation=send_generation,
                log_extra=log_extra,
            )
        _invalidate_resend_probe(error)
        return {"success": False, "retry_wait": True, "error": error}

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
            idempotency_key=stable_idempotency_key,
        )
    except Exception as exc:  # defensive — send_assessment_invite catches internally
        result = {"success": False, "error": str(exc), "retryable": True, "rate_limited": False}

    if result.get("success") and assessment_id and not result.get("email_id"):
        # A provider acknowledgement without its message id cannot be tracked
        # by delivery webhooks. Treat it like a lost response and retry with the
        # same idempotency key instead of recording a false terminal send.
        result = {
            "success": False,
            "error": "Resend accepted the invite without returning an email id",
            "retryable": True,
            "rate_limited": False,
        }

    if result.get("success"):
        logger.info(f"Assessment email sent to {candidate_email}", extra=log_extra)
        if assessment_id:
            confirmation = _confirm_invite_provider_success(
                int(assessment_id),
                email_id=str(result.get("email_id") or ""),
                expected_generation=int(send_generation or 0),
                log_extra=log_extra,
            )
            if not confirmation.get("confirmed"):
                # Superseded/voided generations are intentionally ignored. Any
                # other writeback failure must remain recoverable with the same
                # provider idempotency key; never report a false local send.
                reason = str(confirmation.get("reason") or "")
                if reason not in {"superseded_generation", "voided"}:
                    next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=_EMAIL_RECOVERY_COOLDOWN_SECONDS
                    )
                    _persist_invite_email_state(
                        int(assessment_id),
                        status="retry_wait",
                        next_attempt_at=next_attempt_at,
                        claimed_at=None,
                        last_error="Provider accepted email; local confirmation is pending recovery",
                        expected_generation=send_generation,
                        log_extra=log_extra,
                    )
                    return {
                        **result,
                        "success": False,
                        "provider_accepted": True,
                        "retry_wait": True,
                        "confirmation_error": reason or "writeback_failed",
                    }
            elif confirmation.get("handoff_pending"):
                try:
                    dispatch_assessment_invite_workable_handoff.delay(
                        int(assessment_id), int(send_generation or 0)
                    )
                except Exception:
                    # Provider/local confirmation already committed. The
                    # Workable outbox remains pending for its own Beat sweep.
                    logger.exception(
                        "assessment invite Workable handoff kick failed assessment_id=%s",
                        assessment_id,
                        extra=log_extra,
                    )
        return result

    # ---- send failed ----
    error = str(result.get("error") or "Email send failed")
    rate_limited = bool(result.get("rate_limited"))
    permanent_4xx = _permanent_provider_4xx(result)
    # Unknown failures are recoverable by default.  ``retryable=False`` is
    # terminal only when backed by an explicit permanent provider 4xx.
    retryable = not permanent_4xx

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
        if assessment_id:
            _persist_invite_email_state(
                int(assessment_id),
                status="retrying",
                retry_count=self.request.retries + 1,
                next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=countdown),
                claimed_at=datetime.now(timezone.utc),
                last_error=error,
                expected_generation=send_generation,
                log_extra=log_extra,
            )
        raise self.retry(exc=Exception(error), countdown=countdown)

    if permanent_4xx:
        logger.error(
            "Assessment email to %s permanently rejected after %d attempt(s): %s",
            candidate_email,
            self.request.retries + 1,
            error,
            extra=log_extra,
        )
        if assessment_id:
            _persist_invite_email_state(
                int(assessment_id),
                status="failed",
                next_attempt_at=None,
                claimed_at=None,
                last_error=error,
                expected_generation=send_generation,
                log_extra=log_extra,
            )
        return {"success": False, "failed": True, "email_id": "", "error": error}

    next_attempt_at = datetime.now(timezone.utc) + timedelta(
        seconds=_EMAIL_RECOVERY_COOLDOWN_SECONDS
    )
    logger.error(
        "Assessment email to %s exhausted its short retry chain after %d attempt(s); "
        "durable recovery resumes after %s: %s",
        candidate_email,
        self.request.retries + 1,
        next_attempt_at.isoformat(),
        error,
        extra=log_extra,
    )
    if assessment_id:
        _persist_invite_email_state(
            int(assessment_id),
            status="retry_wait",
            retry_count=self.request.retries + 1,
            next_attempt_at=next_attempt_at,
            claimed_at=None,
            last_error=error,
            expected_generation=send_generation,
            log_extra=log_extra,
        )
    _invalidate_resend_probe(error)
    return {
        "success": False,
        "retry_wait": True,
        "email_id": "",
        "error": error,
        "next_attempt_at": next_attempt_at.isoformat(),
    }


@celery_app.task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    name="app.components.notifications.tasks.dispatch_pending_assessment_invite",
)
def dispatch_pending_assessment_invite(
    self,
    assessment_id: int,
    reply_to: str | None = None,
):
    """Deliver one committed Assessment-backed invite outbox record."""
    from ...platform.database import SessionLocal
    from ...domains.integrations_notifications.invite_flow import (
        deliver_pending_assessment_invite,
    )

    db = SessionLocal()
    try:
        return deliver_pending_assessment_invite(
            db,
            assessment_id=int(assessment_id),
            reply_to=reply_to,
        )
    except Exception as exc:
        logger.exception(
            "assessment invite dispatch failed assessment_id=%s",
            assessment_id,
        )
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    name="app.components.notifications.tasks.sweep_pending_assessment_invites"
)
def sweep_pending_assessment_invites(limit: int = 200) -> dict:
    """Recover committed invite intents whose post-commit kick was lost.

    Fresh ``dispatching`` claims are left alone. A worker crash can strand a
    claim after it committed but before queueing the email, so claims older than
    ten minutes are reset and retried. The operation is bounded and idempotent;
    the per-assessment worker owns the row lock/claim.
    """
    from sqlalchemy import and_, func, or_

    from ...domains.integrations_notifications.invite_flow import (
        INVITE_DISPATCHING,
        INVITE_PENDING_DISPATCH,
    )
    from ...models.assessment import Assessment
    from ...platform.database import SessionLocal

    db = SessionLocal()
    dispatched = 0
    failed = 0
    recovered_claims = 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        rows = (
            db.query(Assessment)
            .filter(
                Assessment.is_voided.is_(False),
                or_(
                    Assessment.invite_email_status == INVITE_PENDING_DISPATCH,
                    and_(
                        Assessment.invite_email_status == INVITE_DISPATCHING,
                        func.coalesce(Assessment.updated_at, Assessment.created_at)
                        < cutoff,
                    ),
                ),
            )
            .order_by(Assessment.created_at.asc(), Assessment.id.asc())
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
        ids: list[int] = []
        for row in rows:
            if row.invite_email_status == INVITE_DISPATCHING:
                row.invite_email_status = INVITE_PENDING_DISPATCH
                recovered_claims += 1
            ids.append(int(row.id))
        db.commit()

        for assessment_id in ids:
            try:
                dispatch_pending_assessment_invite.delay(assessment_id)
                dispatched += 1
            except Exception:
                failed += 1
                logger.exception(
                    "pending assessment invite sweep kick failed assessment_id=%s",
                    assessment_id,
                )
        return {
            "scanned": len(ids),
            "dispatched": dispatched,
            "failed": failed,
            "recovered_claims": recovered_claims,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(
    name="app.components.notifications.tasks.dispatch_assessment_invite_workable_handoff",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_assessment_invite_workable_handoff(
    assessment_id: int, generation: int
) -> dict:
    """Run only the ATS handoff for an already provider-confirmed email."""
    from ...platform.database import SessionLocal
    from ...services.assessment_invite_workable_handoff import (
        assessment_invite_workable_handoff_context,
        defer_assessment_invite_workable_handoff,
        run_assessment_invite_workable_handoff,
    )
    from ...tasks.assessment_tasks import (
        _WORKABLE_ORG_MUTEX_KEY_PREFIX,
        _acquire_workable_org_mutex,
        _release_workable_org_mutex,
        mark_workable_op_pending,
    )

    lookup = SessionLocal()
    try:
        handoff_context = assessment_invite_workable_handoff_context(
            lookup,
            assessment_id=int(assessment_id),
            generation=int(generation),
        )
    finally:
        lookup.close()
    if handoff_context is None:
        return {"status": "missing_or_superseded"}
    organization_id, provider_name = handoff_context
    mutex_namespace = _WORKABLE_ORG_MUTEX_KEY_PREFIX
    if provider_name == "bullhorn":
        from ...components.integrations.bullhorn.sync_runner import (
            BULLHORN_ORG_MUTEX_NAMESPACE,
        )

        mutex_namespace = BULLHORN_ORG_MUTEX_NAMESPACE

    mark_workable_op_pending(int(organization_id))
    mutex = _acquire_workable_org_mutex(
        int(organization_id),
        source=f"{provider_name}_op:assessment_invite_handoff",
        heartbeat=True,
        namespace=mutex_namespace,
    )
    if mutex is None or (mutex is False and provider_name == "bullhorn"):
        db = SessionLocal()
        try:
            return defer_assessment_invite_workable_handoff(
                db,
                assessment_id=int(assessment_id),
                generation=int(generation),
                error=f"{provider_name.title()} is busy; handoff will retry",
            )
        finally:
            db.close()

    db = SessionLocal()
    try:
        return run_assessment_invite_workable_handoff(
            db,
            assessment_id=int(assessment_id),
            generation=int(generation),
        )
    finally:
        db.close()
        _release_workable_org_mutex(mutex)


@celery_app.task(
    name="app.components.notifications.tasks.sweep_assessment_invite_workable_handoffs"
)
def sweep_assessment_invite_workable_handoffs(limit: int = 200) -> dict:
    """Recover lost broker kicks, cooled-down retries, and stale leases."""
    from sqlalchemy import and_, or_

    from ...models.assessment import Assessment
    from ...platform.database import SessionLocal
    from ...services.assessment_invite_workable_handoff import (
        HANDOFF_PENDING,
        HANDOFF_RETRY_WAIT,
        HANDOFF_RUNNING,
    )

    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=_EMAIL_RECOVERY_LEASE_SECONDS)
    db = SessionLocal()
    try:
        rows = (
            db.query(Assessment.id, Assessment.invite_workable_handoff_generation)
            .filter(
                Assessment.is_voided.is_(False),
                Assessment.invite_email_confirmed_generation
                == Assessment.invite_workable_handoff_generation,
                or_(
                    Assessment.invite_workable_handoff_status == HANDOFF_PENDING,
                    and_(
                        Assessment.invite_workable_handoff_status == HANDOFF_RETRY_WAIT,
                        or_(
                            Assessment.invite_workable_handoff_next_attempt_at.is_(None),
                            Assessment.invite_workable_handoff_next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        Assessment.invite_workable_handoff_status == HANDOFF_RUNNING,
                        or_(
                            Assessment.invite_workable_handoff_claimed_at.is_(None),
                            Assessment.invite_workable_handoff_claimed_at <= stale,
                        ),
                    ),
                ),
            )
            .order_by(Assessment.id.asc())
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
    finally:
        db.close()

    dispatched = 0
    failed = 0
    for assessment_id, generation in rows:
        try:
            dispatch_assessment_invite_workable_handoff.delay(
                int(assessment_id), int(generation)
            )
            dispatched += 1
        except Exception:
            failed += 1
            logger.exception(
                "assessment invite Workable handoff sweep kick failed assessment_id=%s",
                assessment_id,
            )
    return {"scanned": len(rows), "dispatched": dispatched, "failed": failed}


def _invite_email_recovery_due(row, *, now: datetime) -> bool:
    """Return whether a recoverable invite row has lost its active worker."""
    from ...domains.integrations_notifications.invite_flow import (
        INVITE_QUEUED,
        INVITE_RETRYING,
        INVITE_RETRY_WAIT,
    )

    status = str(row.invite_email_status or "").strip()
    next_at = _aware(row.invite_email_next_attempt_at)
    claimed_at = _aware(row.invite_email_claimed_at)
    updated_at = _aware(row.updated_at) or _aware(row.created_at)
    lease_cutoff = now - timedelta(seconds=_EMAIL_RECOVERY_LEASE_SECONDS)

    if status == INVITE_RETRY_WAIT:
        # Legacy/partially-migrated retry_wait rows without an explicit due
        # timestamp still recover, but only after the normal cooldown.
        return next_at <= now if next_at is not None else bool(
            updated_at and updated_at <= lease_cutoff
        )
    if status == INVITE_QUEUED:
        return bool((claimed_at or updated_at) and (claimed_at or updated_at) <= lease_cutoff)
    if status == INVITE_RETRYING:
        if next_at is not None:
            return next_at <= (
                now - timedelta(seconds=_EMAIL_RECOVERY_RETRY_GRACE_SECONDS)
            )
        return bool((claimed_at or updated_at) and (claimed_at or updated_at) <= lease_cutoff)
    return False


def _default_worker_resend_ready() -> tuple[bool, str | None]:
    """Gate recovery on a fresh default-worker live Resend probe."""
    from ...services.agent_worker_health import DEFAULT_QUEUE, worker_beat_status

    health = worker_beat_status(required_queues=(DEFAULT_QUEUE,))
    queue = (health.get("queues") or {}).get(DEFAULT_QUEUE) or {}
    if queue.get("heartbeat_fresh") is not True:
        return False, str(queue.get("reason") or health.get("reason") or "heartbeat_unavailable")
    capabilities = queue.get("capabilities") or {}
    if capabilities.get("resend_probe_ok") is not True:
        return False, "resend_probe_failed"
    return True, None


@celery_app.task(
    name="app.components.notifications.tasks.sweep_retryable_assessment_invites"
)
def sweep_retryable_assessment_invites(limit: int = 200) -> dict:
    """Lease and recover transient/lost assessment-invite provider sends.

    The Assessment row is the durable source of truth.  A fresh worker lease or
    scheduled Celery retry is never disturbed; only cooled-down or stale work
    is claimed.  Recovery remains closed while the default worker's live
    Resend canary is unhealthy, avoiding a provider-outage retry storm.
    """
    from sqlalchemy import and_, func, or_
    from sqlalchemy.orm import joinedload

    from ...domains.integrations_notifications.invite_flow import (
        INVITE_DISPATCH_FAILED,
        INVITE_QUEUED,
        INVITE_RETRYING,
        INVITE_RETRY_WAIT,
        _resolve_candidate_facing_brand,
        assessment_invite_idempotency_key,
    )
    from ...models.assessment import Assessment
    from ...platform.database import SessionLocal

    ready, gate_reason = _default_worker_resend_ready()
    if not ready:
        return {
            "gated": True,
            "reason": gate_reason,
            "scanned": 0,
            "leased": 0,
            "dispatched": 0,
            "failed": 0,
        }

    bounded_limit = max(1, min(int(limit), 1000))
    now = datetime.now(timezone.utc)
    lease_cutoff = now - timedelta(seconds=_EMAIL_RECOVERY_LEASE_SECONDS)
    retry_cutoff = now - timedelta(seconds=_EMAIL_RECOVERY_RETRY_GRACE_SECONDS)
    activity_at = func.coalesce(
        Assessment.invite_email_claimed_at,
        Assessment.updated_at,
        Assessment.created_at,
    )
    db = SessionLocal()
    payloads: list[dict] = []
    invalid = 0
    try:
        rows = (
            db.query(Assessment)
            .options(
                joinedload(Assessment.candidate),
                joinedload(Assessment.task),
                joinedload(Assessment.organization),
            )
            .filter(
                Assessment.is_voided.is_(False),
                or_(
                    and_(
                        Assessment.invite_email_status == INVITE_RETRY_WAIT,
                        or_(
                            Assessment.invite_email_next_attempt_at <= now,
                            and_(
                                Assessment.invite_email_next_attempt_at.is_(None),
                                activity_at <= lease_cutoff,
                            ),
                        ),
                    ),
                    and_(
                        Assessment.invite_email_status == INVITE_QUEUED,
                        activity_at <= lease_cutoff,
                    ),
                    and_(
                        Assessment.invite_email_status == INVITE_RETRYING,
                        or_(
                            and_(
                                Assessment.invite_email_next_attempt_at.is_not(None),
                                Assessment.invite_email_next_attempt_at <= retry_cutoff,
                            ),
                            and_(
                                Assessment.invite_email_next_attempt_at.is_(None),
                                activity_at <= lease_cutoff,
                            ),
                        ),
                    ),
                ),
            )
            .order_by(Assessment.updated_at.asc(), Assessment.id.asc())
            .limit(bounded_limit)
            # PostgreSQL rejects an unscoped FOR UPDATE when joinedload adds
            # nullable outer-join relations.  Only the durable outbox row is
            # part of the lease; candidate/task/org are read-only payloads.
            .with_for_update(of=Assessment, skip_locked=True)
            .all()
        )
        for row in rows:
            if not _invite_email_recovery_due(row, now=now):
                continue
            candidate = row.candidate
            org = row.organization
            if (
                candidate is None
                or not str(candidate.email or "").strip()
                or org is None
                or not str(row.token or "").strip()
            ):
                row.invite_email_status = INVITE_DISPATCH_FAILED
                row.invite_email_claimed_at = None
                row.invite_email_next_attempt_at = None
                row.invite_email_last_error = (
                    "persisted candidate email, organization, or assessment token is missing"
                )
                invalid += 1
                continue

            row.invite_email_status = INVITE_RETRYING
            row.invite_email_claimed_at = now
            row.invite_email_next_attempt_at = None
            row.invite_email_retry_count = 0
            payloads.append(
                {
                    "assessment_id": int(row.id),
                    "candidate_email": str(candidate.email).strip(),
                    "candidate_name": str(candidate.full_name or candidate.email),
                    "token": str(row.token),
                    "org_name": str(org.name),
                    "position": str(
                        row.task.name if row.task is not None else "Technical assessment"
                    ),
                    "candidate_facing_brand": _resolve_candidate_facing_brand(org),
                    "reply_to": row.invite_email_reply_to,
                    "idempotency_key": assessment_invite_idempotency_key(row),
                }
            )
        db.commit()

        dispatched = 0
        failed = 0
        for payload in payloads:
            assessment_id = int(payload["assessment_id"])
            try:
                send_assessment_email.delay(
                    **payload,
                    recovery_dispatch=True,
                    request_id=f"invite-recovery/{assessment_id}",
                )
                dispatched += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "assessment invite recovery enqueue failed assessment_id=%s",
                    assessment_id,
                )
                # Release the lease into a cooldown so a broker outage cannot
                # strand it and cannot cause a tight Beat retry loop.
                row = (
                    db.query(Assessment)
                    .filter(Assessment.id == assessment_id)
                    .with_for_update()
                    .one_or_none()
                )
                if row is not None and row.invite_email_status == INVITE_RETRYING:
                    row.invite_email_status = INVITE_RETRY_WAIT
                    row.invite_email_claimed_at = None
                    row.invite_email_next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=_EMAIL_RECOVERY_COOLDOWN_SECONDS
                    )
                    row.invite_email_last_error = str(exc)[:4000]
                    db.commit()

        return {
            "gated": False,
            "reason": None,
            "scanned": len(rows),
            "leased": len(payloads),
            "dispatched": dispatched,
            "failed": failed + invalid,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
