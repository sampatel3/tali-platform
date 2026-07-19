"""Bounded, preservation-first reconciliation receipt histories.

The application event stream is the durable, unbounded audit record. Receipt
histories are a convenient hot-path projection, so they must remain small
without ever trimming evidence that was already retained.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException


MAX_RECONCILIATION_HISTORY_ENTRIES = 100
MAX_RECONCILIATION_HISTORY_BYTES = 128 * 1024
RECONCILIATION_HISTORY_SATURATION_KEY = "reconciliation_history_saturation"


class ReconciliationHistoryError(ValueError):
    """Base class for stored reconciliation-history safety failures."""


class MalformedReconciliationHistory(ReconciliationHistoryError):
    """Stored history cannot be safely interpreted without losing evidence."""


class ReconciliationHistoryFull(ReconciliationHistoryError):
    """Stored history has no room for another retained projection entry."""


@dataclass(frozen=True)
class ReconciliationHistoryAppend:
    appended: bool
    retained_entries: int
    retained_bytes: int


def _malformed() -> MalformedReconciliationHistory:
    return MalformedReconciliationHistory(
        "Stored reconciliation history is malformed; no evidence was overwritten."
    )


def _full() -> ReconciliationHistoryFull:
    return ReconciliationHistoryFull(
        "Reconciliation history reached its retained-evidence limit; contact support."
    )


def _serialized_bytes(value: Any) -> int:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _malformed() from exc
    return len(encoded)


def _validated_saturation(
    receipt: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    raw = receipt.get(RECONCILIATION_HISTORY_SATURATION_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, dict)
        for key, value in raw.items()
    ):
        raise _malformed()
    return dict(raw)


def validated_reconciliation_history(
    receipt: dict[str, Any], history_key: str
) -> tuple[list[dict[str, Any]], int]:
    """Return a shallow projection copy after strict shape/size validation."""

    raw = receipt.get(history_key)
    if raw is None:
        history: list[dict[str, Any]] = []
    elif isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
        # Never normalize, filter, trim, or rewrite retained evidence.
        history = list(raw)
    else:
        raise _malformed()
    size = _serialized_bytes(history)
    if (
        len(history) > MAX_RECONCILIATION_HISTORY_ENTRIES
        or size > MAX_RECONCILIATION_HISTORY_BYTES
    ):
        raise _full()
    _validated_saturation(receipt)
    return history, size


def require_reconciliation_history_capacity(
    receipt: dict[str, Any], history_key: str
) -> None:
    """Fail before external work when a hot receipt projection cannot grow."""

    history, size = validated_reconciliation_history(receipt, history_key)
    saturation = _validated_saturation(receipt)
    if (
        history_key in saturation
        or len(history) >= MAX_RECONCILIATION_HISTORY_ENTRIES
        or size >= MAX_RECONCILIATION_HISTORY_BYTES
    ):
        raise _full()


def _mark_saturated(
    receipt: dict[str, Any],
    *,
    history_key: str,
    reason: str,
    retained_entries: int,
    retained_bytes: int,
    attempted_bytes: int,
    saturated_at: str,
) -> None:
    saturation = _validated_saturation(receipt)
    if history_key in saturation:
        return
    saturation[history_key] = {
        "reason": reason,
        "retained_entries": retained_entries,
        "retained_bytes": retained_bytes,
        "attempted_serialized_bytes": attempted_bytes,
        "max_entries": MAX_RECONCILIATION_HISTORY_ENTRIES,
        "max_bytes": MAX_RECONCILIATION_HISTORY_BYTES,
        "saturated_at": saturated_at,
    }
    receipt[RECONCILIATION_HISTORY_SATURATION_KEY] = saturation


def append_reconciliation_history(
    receipt: dict[str, Any],
    *,
    history_key: str,
    entry: dict[str, Any],
    saturated_at: str,
) -> ReconciliationHistoryAppend:
    """Append when bounded, otherwise preserve prior history and mark it full.

    The caller must always retain ``entry`` in its latest-evidence field and
    immutable application event. This helper only controls the bounded receipt
    projection.
    """

    if not isinstance(entry, dict):
        raise _malformed()
    history, retained_bytes = validated_reconciliation_history(receipt, history_key)
    saturation = _validated_saturation(receipt)
    candidate = [*history, entry]
    attempted_bytes = _serialized_bytes(candidate)
    reason: str | None = None
    if history_key in saturation:
        reason = "already_saturated"
    elif len(history) >= MAX_RECONCILIATION_HISTORY_ENTRIES:
        reason = "entry_limit"
    elif retained_bytes >= MAX_RECONCILIATION_HISTORY_BYTES:
        reason = "byte_limit"
    elif attempted_bytes > MAX_RECONCILIATION_HISTORY_BYTES:
        reason = "candidate_exceeds_byte_limit"

    if reason is not None:
        _mark_saturated(
            receipt,
            history_key=history_key,
            reason=reason,
            retained_entries=len(history),
            retained_bytes=retained_bytes,
            attempted_bytes=attempted_bytes,
            saturated_at=saturated_at,
        )
        return ReconciliationHistoryAppend(
            appended=False,
            retained_entries=len(history),
            retained_bytes=retained_bytes,
        )

    receipt[history_key] = candidate
    return ReconciliationHistoryAppend(
        appended=True,
        retained_entries=len(candidate),
        retained_bytes=attempted_bytes,
    )


def require_reconciliation_history_capacity_or_conflict(
    receipt: dict[str, Any], history_key: str
) -> None:
    """FastAPI adapter used by reconciliation service boundaries."""

    try:
        require_reconciliation_history_capacity(receipt, history_key)
    except ReconciliationHistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


def append_reconciliation_history_or_conflict(
    receipt: dict[str, Any],
    *,
    history_key: str,
    entry: dict[str, Any],
    saturated_at: str,
) -> ReconciliationHistoryAppend:
    """FastAPI adapter that retains the preservation-first append result."""

    try:
        return append_reconciliation_history(
            receipt,
            history_key=history_key,
            entry=entry,
            saturated_at=saturated_at,
        )
    except ReconciliationHistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


__all__ = [
    "MAX_RECONCILIATION_HISTORY_BYTES",
    "MAX_RECONCILIATION_HISTORY_ENTRIES",
    "MalformedReconciliationHistory",
    "RECONCILIATION_HISTORY_SATURATION_KEY",
    "ReconciliationHistoryAppend",
    "ReconciliationHistoryError",
    "ReconciliationHistoryFull",
    "append_reconciliation_history",
    "append_reconciliation_history_or_conflict",
    "require_reconciliation_history_capacity",
    "require_reconciliation_history_capacity_or_conflict",
    "validated_reconciliation_history",
]
