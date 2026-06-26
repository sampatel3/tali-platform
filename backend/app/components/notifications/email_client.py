"""
Resend email service for assessment invitations and notifications.

Handles transactional email delivery for candidate invitations and
hiring-manager result notifications via the Resend API.
"""

import logging
import random
import re
import time

import resend

from ...platform.brand import BRAND_NAME, brand_email_from
from .templates import (
    assessment_expiry_reminder_html,
    assessment_invite_html,
    assessment_invite_text,
    email_verification_html,
    password_reset_html,
    results_notification_html,
)


_ANGLE_ADDR_RE = re.compile(r"<([^>]+)>")


def _extract_address(value: str) -> str:
    """Pull just the email address out of a ``Display Name <addr@x.com>`` string."""
    if not value:
        return ""
    match = _ANGLE_ADDR_RE.search(value)
    if match:
        return match.group(1).strip()
    return value.strip()


def _compose_from(*, base: str, display_name: str | None) -> str:
    """Build a Resend-compatible ``"Name <addr@x.com>"`` from-line.

    When ``display_name`` is set, the inbox shows that name (e.g. the
    org's candidate-facing brand) even though the underlying domain is
    Taali's. Falls back to ``base`` unchanged when no name is provided
    OR when ``base`` doesn't contain a recognizable email address.
    """
    name = (display_name or "").strip()
    if not name:
        return base
    address = _extract_address(base)
    if not address or "@" not in address:
        return base
    # Quote display name only if it contains characters that require it.
    # Keep it simple — most org names are safe.
    safe_name = name.replace('"', "")
    return f'"{safe_name}" <{address}>'

logger = logging.getLogger(__name__)


# --- Resend send resilience -------------------------------------------------
#
# Resend's default API limit is ~2 requests/second. A bulk "send assessment"
# approval in the Home review queue fans out 10-12 invite sends near-
# simultaneously (one Celery task each), so several reliably trip Resend's
# 429 rate limit. Previously ``resend.Emails.send`` was a single unguarded
# call: a 429 became a silent ``logger.error`` and the invite was dropped —
# never delivered, never surfaced, never retried (the 2026-06-25 incident).
#
# Sends now go through ``_send_resend_email``, which retries in-process with
# bounded exponential backoff + jitter. The SDK (resend==2.4.0) raises
# ``ResendError`` with ``.code`` set to the HTTP status but DISCARDS the
# response, so the ``Retry-After`` header isn't available — we honor it if a
# future SDK version exposes it (a ``retry_after`` attribute) and otherwise
# fall back to backoff calibrated to clear the per-second limit. Anything still
# failing after the in-process budget is reported up (``rate_limited`` /
# ``retryable`` flags) so the Celery task can re-queue and, once exhausted,
# surface a ``failed`` status to the recruiter rather than swallow it.
_RETRYABLE_STATUS_CODES = {"429", "500", "502", "503", "504"}
_MAX_SEND_ATTEMPTS = 4
_BASE_BACKOFF_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 8.0


def _send_error_code(exc: Exception) -> str:
    return str(getattr(exc, "code", "") or "").strip()


def _is_rate_limit_error(exc: Exception) -> bool:
    if _send_error_code(exc) == "429":
        return True
    # Defensive: some SDK/proxy paths surface the limit only in the message.
    text = str(exc).lower()
    return "rate limit" in text or "too many requests" in text


def classify_send_error(exc: Exception) -> tuple[bool, bool]:
    """Return ``(retryable, is_rate_limit)`` for a Resend send exception.

    Rate-limit (429) and transient 5xx are retryable; auth/validation (4xx)
    are permanent and must not be retried. Bare network errors (timeout /
    connection reset) are treated as transient too.
    """
    if _is_rate_limit_error(exc):
        return True, True
    if _send_error_code(exc) in _RETRYABLE_STATUS_CODES:
        return True, False
    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name:
        return True, False
    return False, False


def _retry_after_seconds(exc: Exception) -> float | None:
    """Best-effort Retry-After (seconds). None unless the SDK exposes it."""
    val = getattr(exc, "retry_after", None)
    try:
        return max(0.0, float(val)) if val is not None else None
    except (TypeError, ValueError):
        return None


def _send_backoff_seconds(attempt: int, exc: Exception) -> float:
    """Backoff before the next attempt (1-indexed). Honors Retry-After when
    present, otherwise capped exponential backoff; jitter de-syncs the burst."""
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        base = min(retry_after, _MAX_BACKOFF_SECONDS)
    else:
        base = min(_BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)
    return base + random.uniform(0.0, 0.4)


def _send_resend_email(payload: dict, *, recipient: str) -> dict:
    """Send one email via Resend with bounded retry/backoff on 429 + transient
    5xx. Returns the raw Resend response on success; re-raises the last
    exception once the in-process attempt budget is spent (the caller maps that
    to a failure result)."""
    for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
        try:
            return resend.Emails.send(payload)
        except Exception as exc:  # noqa: BLE001 — classified below, re-raised if permanent
            retryable, is_rate_limit = classify_send_error(exc)
            if not retryable or attempt >= _MAX_SEND_ATTEMPTS:
                raise
            delay = _send_backoff_seconds(attempt, exc)
            logger.warning(
                "Resend send to %s hit %s (attempt %d/%d) — retrying in %.2fs: %s",
                recipient,
                "rate limit (429)" if is_rate_limit else "a transient error",
                attempt,
                _MAX_SEND_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover — loop returns or raises


class EmailService:
    """Service for sending transactional emails through Resend."""

    def __init__(self, api_key: str, from_email: str = brand_email_from()):
        resend.api_key = api_key
        self.from_email = from_email
        logger.info("EmailService initialised (from=%s)", self.from_email)

    def send_assessment_invite(
        self,
        candidate_email: str,
        candidate_name: str,
        token: str,
        assessment_id: int | None,
        org_name: str,
        position: str,
        frontend_url: str,
        candidate_facing_brand: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Send the assessment invite email, co-branded with the org.

        ``candidate_facing_brand``: the name candidates know the org by
        (e.g. "Acme Hiring"). When set, becomes the inbox display name on
        the from-line so the email looks like a continuation of the
        recruiter's prior comms (Workable application receipt, etc.).
        Falls back to ``org_name`` when not set, and the platform brand
        when neither is.

        ``reply_to``: optional; usually the recruiter's email so candidate
        replies route to a real person rather than the no-reply address.
        """
        try:
            if assessment_id is not None:
                assessment_link = f"{frontend_url}/assessment/{assessment_id}?token={token}"
            else:
                assessment_link = f"{frontend_url}/assess/{token}"
            display_brand = (candidate_facing_brand or org_name or "").strip() or BRAND_NAME
            logger.info(
                "Sending assessment invite to %s for position '%s' at %s (brand=%s)",
                candidate_email, position, org_name, display_brand,
            )

            html_body = assessment_invite_html(
                candidate_name=candidate_name,
                org_name=display_brand,
                position=position,
                assessment_link=assessment_link,
            )
            text_body = assessment_invite_text(
                candidate_name=candidate_name,
                org_name=display_brand,
                position=position,
                assessment_link=assessment_link,
            )

            payload: dict = {
                "from": _compose_from(base=self.from_email, display_name=display_brand),
                "to": [candidate_email],
                "subject": f"Your {position} assessment at {display_brand}",
                "html": html_body,
                "text": text_body,
            }
            reply_to_clean = (reply_to or "").strip()
            if reply_to_clean:
                payload["reply_to"] = reply_to_clean

            email = _send_resend_email(payload, recipient=candidate_email)

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Assessment invite sent successfully (email_id=%s, to=%s)", email_id, candidate_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            retryable, is_rate_limit = classify_send_error(e)
            logger.error(
                "Failed to send assessment invite to %s (rate_limited=%s, retryable=%s): %s",
                candidate_email, is_rate_limit, retryable, str(e),
            )
            return {
                "success": False,
                "email_id": "",
                "error": str(e),
                "rate_limited": is_rate_limit,
                "retryable": retryable,
                "retry_after": _retry_after_seconds(e),
            }

    def send_results_notification(
        self,
        user_email: str,
        candidate_name: str,
        score: float,
        assessment_id: int,
        frontend_url: str,
    ) -> dict:
        try:
            results_link = f"{frontend_url}/assessments/{assessment_id}"
            logger.info("Sending results notification to %s for candidate '%s'", user_email, candidate_name)

            html_body = results_notification_html(
                candidate_name=candidate_name,
                score=score,
                results_link=results_link,
            )

            email = _send_resend_email({
                "from": self.from_email,
                "to": [user_email],
                "subject": f"Assessment Complete: {candidate_name} — {score:.0f}%",
                "html": html_body,
            }, recipient=user_email)

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Results notification sent successfully (email_id=%s, to=%s)", email_id, user_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send results notification to %s: %s", user_email, str(e))
            return {"success": False, "email_id": ""}

    def send_email_verification(self, to_email: str, full_name: str, verification_link: str) -> dict:
        try:
            logger.info("Sending email verification to %s", to_email)
            html_body = email_verification_html(full_name=full_name, verification_link=verification_link)

            email = _send_resend_email({
                "from": self.from_email,
                "to": [to_email],
                "subject": f"{BRAND_NAME} — Verify your email address",
                "html": html_body,
            }, recipient=to_email)

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Verification email sent (email_id=%s, to=%s)", email_id, to_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send verification email to %s: %s", to_email, str(e))
            return {"success": False, "email_id": ""}

    def send_password_reset(self, to_email: str, reset_link: str) -> dict:
        try:
            logger.info("Sending password reset email to %s", to_email)
            html_body = password_reset_html(reset_link=reset_link)

            email = _send_resend_email({
                "from": self.from_email,
                "to": [to_email],
                "subject": f"{BRAND_NAME} — Reset your password",
                "html": html_body,
            }, recipient=to_email)

            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            logger.info("Password reset email sent (email_id=%s, to=%s)", email_id, to_email)
            return {"success": True, "email_id": email_id}
        except Exception as e:
            logger.error("Failed to send password reset to %s: %s", to_email, str(e))
            return {"success": False, "email_id": ""}

    def send_assessment_expiry_reminder(
        self,
        candidate_email: str,
        candidate_name: str,
        task_name: str,
        assessment_link: str,
        expiry_text: str,
    ) -> dict:
        try:
            logger.info("Sending assessment expiry reminder to %s", candidate_email)
            html_body = assessment_expiry_reminder_html(
                candidate_name=candidate_name,
                task_name=task_name,
                assessment_link=assessment_link,
                expiry_text=expiry_text,
            )
            email = _send_resend_email({
                "from": self.from_email,
                "to": [candidate_email],
                "subject": f"Your {BRAND_NAME} assessment expires soon",
                "html": html_body,
            }, recipient=candidate_email)
            email_id = email.get("id", "") if isinstance(email, dict) else str(email)
            return {"success": True, "email_id": email_id}
        except Exception as exc:
            logger.error("Failed to send expiry reminder to %s: %s", candidate_email, str(exc))
            return {"success": False, "email_id": ""}

