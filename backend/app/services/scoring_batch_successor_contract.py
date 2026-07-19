"""Validation helpers for durable scoring-successor JSON receipts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .scoring_batch_successors import CLAIM_SECONDS, successor_payload


_RECOVERY_DEFER_SECONDS = 60
_MAX_FUTURE_METADATA_SECONDS = max(CLAIM_SECONDS, _RECOVERY_DEFER_SECONDS) * 2


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def scoring_successor_contract_error(
    value: object,
    *,
    role_id: int = 0,
    organization_id: int = 0,
    now: datetime | None = None,
) -> str | None:
    payload = successor_payload(value)
    if payload is None:
        return "invalid_successor_payload"
    dispatch_attempt = payload.get("dispatch_attempt")
    if dispatch_attempt is not None and (
        type(dispatch_attempt) is not int or dispatch_attempt < 0
    ):
        return "invalid_dispatch_attempt"
    applied_after = payload.get("applied_after")
    if applied_after is not None and _timestamp(applied_after) is None:
        return "invalid_applied_after"
    current = now or datetime.now(timezone.utc)
    for key in ("claimed_at", "reconcile_after"):
        value = payload.get(key)
        if value is None:
            continue
        parsed = _timestamp(value)
        if parsed is None:
            return f"invalid_{key}"
        if parsed > current + timedelta(seconds=_MAX_FUTURE_METADATA_SECONDS):
            return f"invalid_future_{key}"
    queue_id = str(payload.get("queue_id") or "")
    attempt = dispatch_attempt if type(dispatch_attempt) is int else 0
    if len(f"scoring-batch:{organization_id}:{role_id}:{queue_id}:{attempt}") > 200:
        return "successor_queue_id_too_long"
    return None


def scoring_successor_reconcile_after(value: object) -> datetime | None:
    payload = successor_payload(value)
    return _timestamp(payload.get("reconcile_after")) if payload is not None else None


__all__ = [
    "scoring_successor_contract_error",
    "scoring_successor_reconcile_after",
]
