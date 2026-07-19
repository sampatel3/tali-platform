"""Durable outbox for Workable Assessments-Provider result callbacks.

``enqueue`` writes a pending ``workable_webhook_outbox`` row, idempotent on
``dedup_key``. ``drain`` ``PUT``s pending rows to each row's ``callback_url``
and marks them ``sent``; a send that doesn't land leaves the row pending (until
a retry cap) so a result is never silently dropped. Mirrors
``app.brain_feed.outbox``.

Gated by ``WORKABLE_PROVIDER_ENABLED`` (default off): when off, ``drain`` is a
no-op, so the live platform makes no outbound calls until the integration is
deliberately enabled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...models.organization import Organization
from ...models.workable_webhook_outbox import (
    WORKABLE_OUTBOX_KINDS,
    WORKABLE_OUTBOX_STATUS_FAILED,
    WORKABLE_OUTBOX_STATUS_PENDING,
    WORKABLE_OUTBOX_STATUS_PROCESSING,
    WORKABLE_OUTBOX_STATUS_SENT,
    WorkableWebhookOutbox,
)
from ...platform.config import settings
from ...platform.secrets import decrypt_integration_secret
from ...components.integrations.workable.url_security import validate_workable_callback_url

logger = logging.getLogger("taali.workable_provider.outbox")

_MAX_ATTEMPTS = 8
_DRAIN_BATCH_SIZE = 100
_PUT_TIMEOUT_SECONDS = 10.0
_LEASE_SECONDS = 120
_CALLBACK_ERROR = "workable_callback_delivery_failed"


@dataclass(frozen=True)
class _CallbackClaim:
    """Primitive lease snapshot; secrets are intentionally omitted from repr."""

    row_id: int
    organization_id: int
    event_kind: str
    dedup_key: str
    callback_url: str
    payload: dict[str, Any]
    attempt: int
    callback_token: str | None = field(repr=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    *,
    organization_id: int,
    event_kind: str,
    dedup_key: str,
    callback_url: str,
    payload: dict[str, Any],
) -> Optional[WorkableWebhookOutbox]:
    """Insert one pending outbox row. Idempotent on ``dedup_key`` (a re-sweep
    of the same source row is a no-op). Returns the newly-created row or None."""
    if not settings.WORKABLE_PROVIDER_ENABLED:
        raise RuntimeError("Workable provider is disabled")
    if event_kind not in WORKABLE_OUTBOX_KINDS:
        raise ValueError(f"unknown workable outbox event_kind: {event_kind!r}")
    callback_url = validate_workable_callback_url(callback_url)
    existing = (
        db.query(WorkableWebhookOutbox)
        .filter(WorkableWebhookOutbox.dedup_key == dedup_key)
        .one_or_none()
    )
    if existing is not None:
        return None
    row = WorkableWebhookOutbox(
        organization_id=organization_id,
        event_kind=event_kind,
        dedup_key=dedup_key,
        callback_url=callback_url,
        payload=payload,
        status=WORKABLE_OUTBOX_STATUS_PENDING,
        attempts=0,
    )
    db.add(row)
    db.flush()
    return row


def _callback_token(db: Session, organization_id: int) -> str:
    """The bearer token Workable issued for this org's callbacks, if any.

    Stored in ``organizations.workable_provider_config.callback_auth_token``.
    Workable's exact callback-auth scheme is partner-gated; when no token is
    configured we PUT without an Authorization header (some callback URLs are
    pre-authenticated). Confirm + harden during Workable QA.
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .first()
    )
    cfg = (org.workable_provider_config or {}) if org else {}
    return decrypt_integration_secret(
        str(cfg.get("callback_auth_token") or "").strip(),
        allow_plaintext=True,
    )


def _put(row: _CallbackClaim, token: str) -> None:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    callback_url = validate_workable_callback_url(row.callback_url)
    resp = httpx.put(
        callback_url,
        json=row.payload,
        headers=headers,
        timeout=_PUT_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()


def _retry_delay(attempts: int, row_id: int) -> int:
    base = min(1800, 30 * (2 ** max(0, attempts - 1)))
    return base + ((int(row_id) * 37 + attempts * 17) % 16)


def _eligible(now: datetime):
    return or_(
        and_(
            WorkableWebhookOutbox.status == WORKABLE_OUTBOX_STATUS_PENDING,
            or_(
                WorkableWebhookOutbox.next_attempt_at.is_(None),
                WorkableWebhookOutbox.next_attempt_at <= now,
            ),
        ),
        and_(
            WorkableWebhookOutbox.status == WORKABLE_OUTBOX_STATUS_PROCESSING,
            or_(
                WorkableWebhookOutbox.lease_until.is_(None),
                WorkableWebhookOutbox.lease_until <= now,
            ),
        ),
    )


def _claim(db: Session, *, batch_size: int) -> list[_CallbackClaim]:
    now = _now()
    rows = (
        db.query(WorkableWebhookOutbox)
        .filter(_eligible(now))
        .order_by(WorkableWebhookOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(batch_size)))
        .all()
    )
    lease_seconds = max(
        _LEASE_SECONDS,
        int(len(rows) * (_PUT_TIMEOUT_SECONDS + 2) + 30),
    )
    lease_until = now + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = WORKABLE_OUTBOX_STATUS_PROCESSING
        row.attempts = int(row.attempts or 0) + 1
        row.lease_until = lease_until
        row.next_attempt_at = None
    token_cache: dict[int, str | None] = {}
    claims: list[_CallbackClaim] = []
    for row in rows:
        organization_id = int(row.organization_id)
        if organization_id not in token_cache:
            try:
                token_cache[organization_id] = _callback_token(db, organization_id)
            except Exception as exc:
                logger.warning(
                    "workable callback credential unavailable org_id=%s error_type=%s",
                    organization_id,
                    type(exc).__name__,
                )
                token_cache[organization_id] = None
        claims.append(
            _CallbackClaim(
                row_id=int(row.id),
                organization_id=organization_id,
                event_kind=str(row.event_kind),
                dedup_key=str(row.dedup_key),
                callback_url=str(row.callback_url),
                payload=dict(row.payload or {}),
                attempt=int(row.attempts),
                callback_token=token_cache[organization_id],
            )
        )
    db.commit()
    return claims


def _finalize_claim(
    db: Session,
    *,
    claim: _CallbackClaim,
    delivered: bool,
    max_attempts: int,
    now: datetime,
) -> str:
    row = (
        db.query(WorkableWebhookOutbox)
        .filter(WorkableWebhookOutbox.id == int(claim.row_id))
        .with_for_update()
        .one_or_none()
    )
    if (
        row is None
        or row.status != WORKABLE_OUTBOX_STATUS_PROCESSING
        or int(row.attempts or 0) != int(claim.attempt)
    ):
        db.rollback()
        return "stale"
    if delivered:
        row.status = WORKABLE_OUTBOX_STATUS_SENT
        row.sent_at = now
        row.last_error = None
        row.next_attempt_at = None
        outcome = "sent"
    else:
        row.last_error = _CALLBACK_ERROR
        if int(row.attempts or 0) >= int(max_attempts):
            row.status = WORKABLE_OUTBOX_STATUS_FAILED
            row.next_attempt_at = None
            outcome = "failed"
        else:
            row.status = WORKABLE_OUTBOX_STATUS_PENDING
            row.next_attempt_at = now + timedelta(
                seconds=_retry_delay(int(row.attempts), int(row.id))
            )
            outcome = "pending"
    row.updated_at = now
    row.lease_until = None
    db.commit()
    return outcome


def drain(
    db: Session,
    *,
    batch_size: int = _DRAIN_BATCH_SIZE,
    max_attempts: int = _MAX_ATTEMPTS,
) -> dict:
    """PUT pending result callbacks to Workable. Idempotent + retry-safe.

    No-op (``status='disabled'``) when WORKABLE_PROVIDER_ENABLED is off.
    """
    if not settings.WORKABLE_PROVIDER_ENABLED:
        return {"status": "disabled", "scanned": 0, "sent": 0, "failed": 0}

    rows = _claim(db, batch_size=batch_size)
    sent = failed = still_pending = 0
    for row in rows:
        now = _now()
        delivered = False
        if db.in_transaction():
            raise RuntimeError("Workable callback started in a DB transaction")
        try:
            if row.callback_token is None:
                raise RuntimeError("Workable callback credential unavailable")
            _put(row, row.callback_token)
            delivered = True
        except Exception as exc:
            logger.exception(
                "workable callback delivery failed row_id=%s error_type=%s",
                row.row_id,
                type(exc).__name__,
            )
        outcome = _finalize_claim(
            db,
            claim=row,
            delivered=delivered,
            max_attempts=int(max_attempts),
            now=now,
        )
        if outcome == "sent":
            sent += 1
        elif outcome == "failed":
            failed += 1
        else:
            still_pending += 1
    if failed:
        logger.warning(
            "workable_provider drain: scanned=%d sent=%d failed=%d pending=%d",
            len(rows), sent, failed, still_pending,
        )
    return {
        "status": "ok",
        "scanned": len(rows),
        "sent": sent,
        "failed": failed,
        "pending": still_pending,
    }


__all__ = ["enqueue", "drain"]
