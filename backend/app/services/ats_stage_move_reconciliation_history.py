"""Exact, preservation-first updates for archived ATS stage receipts."""

from __future__ import annotations

from typing import Any

from .ats_stage_move_receipt import STAGE_MOVE_HISTORY_KEY


class ArchivedStageMoveHistoryError(ValueError):
    """An archived receipt cannot be located or updated without losing evidence."""


def _history(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get(STAGE_MOVE_HISTORY_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ArchivedStageMoveHistoryError(
            "Stored ATS stage-move history is malformed; no evidence was overwritten"
        )
    # The returned list is a new outer projection. Retained receipt dictionaries
    # are deliberately not normalized, filtered, trimmed, or otherwise rewritten.
    return list(raw)


def _matches(
    receipt: dict[str, Any],
    *,
    operation_id: str,
    provider: str,
    provider_target_id: str,
) -> bool:
    return (
        str(receipt.get("operation_id") or "").strip() == operation_id
        and str(receipt.get("provider") or "").strip().lower() == provider
        and str(receipt.get("provider_target_id") or "").strip()
        == provider_target_id
    )


def locate_archived_stage_move_receipt(
    state: dict[str, Any],
    *,
    operation_id: str,
    provider: str,
    provider_target_id: str,
) -> dict[str, Any] | None:
    """Return the newest exact archived receipt after strict outer validation."""

    history = _history(state)
    return next(
        (
            dict(item)
            for item in reversed(history)
            if _matches(
                item,
                operation_id=operation_id,
                provider=provider,
                provider_target_id=provider_target_id,
            )
        ),
        None,
    )


def replace_archived_stage_move_receipt(
    state: dict[str, Any],
    *,
    expected: dict[str, Any],
    replacement: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace one exact slot without appending, trimming, or changing siblings."""

    operation_id = str(expected.get("operation_id") or "").strip()
    provider = str(expected.get("provider") or "").strip().lower()
    provider_target_id = str(expected.get("provider_target_id") or "").strip()
    history = _history(state)
    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if not _matches(
            item,
            operation_id=operation_id,
            provider=provider,
            provider_target_id=provider_target_id,
        ):
            continue
        if item != expected:
            raise ArchivedStageMoveHistoryError(
                "The exact archived stage-move receipt changed during the ATS check"
            )
        history[index] = dict(replacement)
        return history
    raise ArchivedStageMoveHistoryError(
        "The exact archived stage-move receipt changed during the ATS check"
    )


__all__ = [
    "ArchivedStageMoveHistoryError",
    "locate_archived_stage_move_receipt",
    "replace_archived_stage_move_receipt",
]
