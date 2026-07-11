"""Resend delivery-webhook handling.

Resend signs webhooks with Svix. We verify the signature manually (no svix
dependency) and map each event onto the originating ``Assessment`` row —
correlated by the Resend message id we stored at send time
(``Assessment.invite_email_id``). This powers the recruiter's invited-candidate
tracker (delivered / opened / bounced state per invite).

Svix signature scheme (https://docs.svix.com/receiving/verifying-payloads):
- Headers: ``svix-id``, ``svix-timestamp``, ``svix-signature``.
- Secret is ``whsec_<base64>``; the base64 part (after the prefix) is the key.
- signed_content = ``f"{svix_id}.{svix_timestamp}.{body}"`` (body = raw bytes).
- expected = base64(HMAC_SHA256(key, signed_content)).
- ``svix-signature`` is a space-separated list of ``v1,<sig>`` tokens; the
  request is authentic if expected matches any token's signature.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models.assessment import Assessment

logger = logging.getLogger(__name__)

# Tolerance (seconds) for the svix-timestamp vs now, to bound replay attacks.
_TIMESTAMP_TOLERANCE_SECONDS = 5 * 60

# Lifecycle rank so a late-arriving lower event can't downgrade a more
# advanced state (e.g. a 'delivered' landing after 'opened'). Failure states
# (bounced/complained) are handled separately and always win.
_STATUS_RANK = {
    "sent": 1,
    "delivery_delayed": 1,
    "delivered": 2,
    "opened": 3,
    "clicked": 4,
}
_FAILURE_STATUSES = {"bounced", "complained", "failed"}


def _decode_secret(secret: str) -> bytes:
    raw = secret.strip()
    if raw.startswith("whsec_"):
        raw = raw[len("whsec_"):]
    # Svix secrets are standard base64; pad defensively.
    padding = "=" * (-len(raw) % 4)
    return base64.b64decode(raw + padding)


def verify_resend_webhook_signature(
    *,
    secret: str,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    body: bytes,
) -> bool:
    """Return True iff the Svix signature is valid for this payload."""
    if not (secret and svix_id and svix_timestamp and svix_signature):
        return False

    # Replay guard — reject wildly out-of-window timestamps. Be lenient if the
    # timestamp isn't a parseable int (don't hard-fail auth on a format quirk).
    try:
        ts = int(svix_timestamp)
        now = int(datetime.now(timezone.utc).timestamp())
        if abs(now - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
            return False
    except (TypeError, ValueError):
        pass

    try:
        key = _decode_secret(secret)
    except Exception:
        logger.warning("RESEND_WEBHOOK_SECRET is not valid base64")
        return False

    signed_content = b"%s.%s.%s" % (
        svix_id.encode(),
        svix_timestamp.encode(),
        body,
    )
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode()

    # Header is space-separated "v1,<sig>" (versioned) tokens.
    for token in svix_signature.split():
        _, _, sig = token.partition(",")
        if sig and hmac.compare_digest(expected, sig):
            return True
    return False


def _event_email_id(payload: dict[str, Any]) -> Optional[str]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    # Resend uses ``email_id`` in webhook payloads; tolerate ``id`` too.
    val = data.get("email_id") or data.get("id")
    return str(val).strip() if val else None


def _event_recipient(payload: dict[str, Any]) -> Optional[str]:
    """Recipient address from a Resend event payload.

    Resend puts recipients in ``data.to`` (a list, or occasionally a bare
    string). We suppress the first recipient — invites are single-recipient.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    to = data.get("to")
    if isinstance(to, list) and to:
        first = to[0]
        return str(first).strip() if first else None
    if isinstance(to, str) and to.strip():
        return to.strip()
    return None


def _maybe_suppress_global(db: Session, status: str, payload: dict[str, Any]) -> None:
    """On a hard bounce / complaint, add a platform-global suppression.

    Best-effort and non-fatal: a failure here must never break the invite
    delivery tracking that ``apply_resend_event`` performs. Global (org NULL)
    rows protect the shared sender domain across every org.
    """
    if status not in ("bounced", "complained"):
        return
    recipient = _event_recipient(payload)
    if not recipient:
        return
    try:
        # Imported lazily so the webhook service doesn't pull the suppression
        # service (and its settings/config) at module import time.
        from .email_suppression_service import suppress

        suppress(
            db,
            email=recipient,
            reason=status,
            source="webhook",
            organization_id=None,
        )
    except Exception:  # pragma: no cover — never break invite tracking
        logger.warning("Failed to record global suppression for a %s event", status)


def apply_resend_event(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one Resend event to its assessment. Best-effort, idempotent.

    Returns a small status dict for the route's ack body.
    """
    event_type = str(payload.get("type") or "").strip()  # e.g. "email.delivered"
    status = event_type.split(".", 1)[1] if "." in event_type else event_type

    # Deliverability guardrail: a hard bounce / complaint suppresses the address
    # globally regardless of whether we can correlate it to a tracked invite —
    # it protects the shared sender domain. Best-effort; never raises.
    _maybe_suppress_global(db, status, payload)

    email_id = _event_email_id(payload)
    if not email_id:
        return {"status": "ignored", "reason": "no_email_id", "event": event_type}

    asmt = (
        db.query(Assessment)
        .filter(Assessment.invite_email_id == email_id)
        .first()
    )
    if asmt is None:
        # Not one of ours (or sent before tracking existed) — ack so Resend
        # doesn't retry forever.
        return {"status": "ignored", "reason": "no_matching_assessment", "event": event_type}

    now = datetime.now(timezone.utc)
    is_failure = status in _FAILURE_STATUSES

    # Stamp the specific event timestamp.
    if status == "delivered":
        asmt.invite_delivered_at = asmt.invite_delivered_at or now
    elif status in ("opened", "clicked"):
        asmt.invite_opened_at = asmt.invite_opened_at or now
    elif status == "bounced":
        asmt.invite_bounced_at = asmt.invite_bounced_at or now

    # Update the rolled-up status without downgrading progress; failures win.
    current = asmt.invite_email_status or ""
    if is_failure:
        asmt.invite_email_status = status
    elif _STATUS_RANK.get(status, 0) >= _STATUS_RANK.get(current, 0):
        asmt.invite_email_status = status

    db.commit()
    return {
        "status": "applied",
        "event": event_type,
        "assessment_id": int(asmt.id),
        "invite_email_status": asmt.invite_email_status,
    }


__all__ = ["verify_resend_webhook_signature", "apply_resend_event"]
