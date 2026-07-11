"""Email suppression service — normalize, upsert, and check suppressed addresses.

The legal/deliverability guardrail for outreach. Global rows (org NULL) come
from Resend hard-bounce/complaint webhooks and protect the shared sender domain
across every org; org-scoped rows are that org's unsubscribes + manual blocks.

Also holds the signed public-unsubscribe token helpers (HMAC-SHA256 over
``org_id:email`` keyed by ``SECRET_KEY``) — kept here so the token format and
the suppression writer live together. ``itsdangerous`` isn't a dependency, so
the token is hand-rolled with ``hmac.compare_digest`` for constant-time verify.

No LLM calls, no metering — pure data guardrail.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional

from sqlalchemy.orm import Session

from ..models.email_suppression import (
    SUPPRESSION_REASON_RANK,
    EmailSuppression,
)
from ..platform.config import settings


def normalize_email(email: str | None) -> str:
    """Lowercase + trim. The canonical form stored in ``email_normalized``."""
    return (email or "").strip().lower()


def suppress(
    db: Session,
    *,
    email: str,
    reason: str,
    source: str | None = None,
    organization_id: int | None = None,
    note: str | None = None,
) -> EmailSuppression:
    """Idempotent upsert of one suppression row.

    Global rows (``organization_id`` NULL) are deduped in code because Postgres
    treats NULL as distinct in the UNIQUE constraint — a plain INSERT would let
    the same global address in twice. Org-scoped rows rely on the constraint but
    we take the same query-first path uniformly so the behaviour matches on both
    SQLite (tests) and Postgres (prod).

    On conflict, a stronger reason overwrites a weaker one (complained >
    bounced > unsubscribed > manual); a weaker reason leaves the row's reason
    intact but still refreshes source/note/created_at so the record reflects the
    latest event.
    """
    normalized = normalize_email(email)
    existing = (
        db.query(EmailSuppression)
        .filter(
            EmailSuppression.organization_id.is_(None)
            if organization_id is None
            else EmailSuppression.organization_id == organization_id,
            EmailSuppression.email_normalized == normalized,
        )
        .first()
    )

    if existing is not None:
        # Stronger reason wins; equal/weaker keeps the existing reason.
        if SUPPRESSION_REASON_RANK.get(reason, 0) >= SUPPRESSION_REASON_RANK.get(
            existing.reason, 0
        ):
            existing.reason = reason
        existing.source = source
        if note is not None:
            existing.note = note
        db.commit()
        db.refresh(existing)
        return existing

    row = EmailSuppression(
        organization_id=organization_id,
        email_normalized=normalized,
        reason=reason,
        source=source,
        note=note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def is_suppressed(
    db: Session, *, email: str, organization_id: int
) -> Optional[str]:
    """Return the suppression reason if ``email`` is blocked for this org, else None.

    A global row (org NULL) OR an org-scoped row suppresses. When both exist the
    stronger reason is returned so callers surface the most severe cause.
    """
    normalized = normalize_email(email)
    rows = (
        db.query(EmailSuppression)
        .filter(
            EmailSuppression.email_normalized == normalized,
            (EmailSuppression.organization_id.is_(None))
            | (EmailSuppression.organization_id == organization_id),
        )
        .all()
    )
    if not rows:
        return None
    strongest = max(rows, key=lambda r: SUPPRESSION_REASON_RANK.get(r.reason, 0))
    return strongest.reason


def suppressed_set(
    db: Session, *, emails, organization_id: int
) -> dict[str, str]:
    """Bulk suppression check for campaign send paths — no N+1.

    Returns ``{normalized_email: strongest_reason}`` for every input address
    that is suppressed (global OR this org). Addresses that aren't suppressed
    are absent from the map.
    """
    normalized_inputs = {normalize_email(e) for e in emails if normalize_email(e)}
    if not normalized_inputs:
        return {}

    rows = (
        db.query(EmailSuppression)
        .filter(
            EmailSuppression.email_normalized.in_(normalized_inputs),
            (EmailSuppression.organization_id.is_(None))
            | (EmailSuppression.organization_id == organization_id),
        )
        .all()
    )
    result: dict[str, str] = {}
    for row in rows:
        rank = SUPPRESSION_REASON_RANK.get(row.reason, 0)
        current = result.get(row.email_normalized)
        if current is None or rank > SUPPRESSION_REASON_RANK.get(current, 0):
            result[row.email_normalized] = row.reason
    return result


# ---------------------------------------------------------------------------
# Signed public-unsubscribe token
# ---------------------------------------------------------------------------
#
# Format: ``<payload_b64url>.<sig_b64url>`` where payload = ``org_id:email`` and
# sig = HMAC-SHA256(SECRET_KEY, payload). No expiry — an unsubscribe link should
# work indefinitely. Constant-time compare guards against signature tampering.

_TOKEN_SEP = "."


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign_payload(payload: str) -> str:
    sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(sig)


def make_unsubscribe_token(org_id: int, email: str) -> str:
    """Mint a tamper-evident unsubscribe token for ``org_id`` + ``email``."""
    payload = f"{int(org_id)}:{normalize_email(email)}"
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    return f"{payload_b64}{_TOKEN_SEP}{_sign_payload(payload)}"


def verify_unsubscribe_token(token: str) -> Optional[tuple[int, str]]:
    """Return ``(org_id, email)`` if the token is valid, else None.

    Rejects tampered payloads (signature mismatch) and malformed tokens.
    """
    if not token or _TOKEN_SEP not in token:
        return None
    payload_b64, _, sig = token.partition(_TOKEN_SEP)
    if not payload_b64 or not sig:
        return None
    try:
        payload = _b64url_decode(payload_b64).decode("utf-8")
    except Exception:
        return None
    if not hmac.compare_digest(_sign_payload(payload), sig):
        return None
    org_part, _, email = payload.partition(":")
    if not org_part or not email:
        return None
    try:
        org_id = int(org_part)
    except ValueError:
        return None
    return org_id, email


__all__ = [
    "normalize_email",
    "suppress",
    "is_suppressed",
    "suppressed_set",
    "make_unsubscribe_token",
    "verify_unsubscribe_token",
]
