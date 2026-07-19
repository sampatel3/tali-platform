"""Safe, stable errors for Workable-facing state and API responses."""

from __future__ import annotations

import re
from typing import Any

import httpx


_MESSAGES = {
    "workable_authorization_failed": (
        "Workable authorization failed. Reconnect Workable and verify the required API scopes."
    ),
    "workable_rate_limited": (
        "Workable's rate limit was reached. Retry after a short wait."
    ),
    "workable_not_found": (
        "A Workable record was not found. It may have been removed."
    ),
    "workable_unavailable": (
        "Workable is temporarily unavailable. Retry when the service recovers."
    ),
    "workable_network_error": (
        "Workable could not be reached. Check connectivity and retry."
    ),
    "workable_request_rejected": (
        "Workable rejected the request. Review the integration configuration and retry."
    ),
    "workable_invalid_response": (
        "Workable returned an invalid response. Retry the operation."
    ),
    "workable_sync_paused": (
        "Paused for a pending Workable write; remaining records will retry on the next sync."
    ),
    "workable_sync_cancelled": "The sync was cancelled by the user.",
    "workable_sync_worker_failed": (
        "The Workable sync worker stopped before completion. Retry the sync."
    ),
    "workable_sync_stale": (
        "A stale Workable sync was closed safely. Start a new sync."
    ),
    "workable_selected_roles_missing": (
        "Some selected roles were not found in Workable. Refresh the role list and retry."
    ),
    "workable_no_jobs_matched": (
        "No Workable jobs matched the selected roles. Refresh the role list and retry."
    ),
    "workable_no_jobs_available": (
        "Workable returned no jobs. Verify the r_jobs scope and that the account has jobs."
    ),
    "workable_sync_failed": (
        "The Workable sync could not complete. Retry the sync."
    ),
}


def _tag(code: str, message: str | None = None) -> str:
    return f"{code}: {message or _MESSAGES[code]}"


def public_workable_exception(exc: BaseException) -> str:
    """Classify an exception without exposing URLs, payloads, tokens, or SQL."""

    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
    status = response.status_code if response is not None else None
    if status == 429 or type(exc).__name__ == "WorkableRateLimitError":
        return _tag("workable_rate_limited")
    if status in {401, 403}:
        return _tag("workable_authorization_failed")
    if status == 404:
        return _tag("workable_not_found")
    if status is not None and status >= 500:
        return _tag("workable_unavailable")
    if status is not None:
        return _tag("workable_request_rejected")
    if isinstance(exc, httpx.RequestError):
        return _tag("workable_network_error")
    if isinstance(exc, (ValueError, TypeError)):
        return _tag("workable_invalid_response")
    return _tag("workable_sync_failed")


def public_workable_sync_error(value: Any) -> str:
    """Return a safe tagged message for new exceptions and legacy stored text."""

    if isinstance(value, BaseException):
        return public_workable_exception(value)
    text = str(value or "").strip()
    lower = text.lower()
    if not text:
        return _tag("workable_sync_failed")
    for code, message in _MESSAGES.items():
        if lower.startswith(f"{code}:"):
            return _tag(code, text.split(":", 1)[1].strip() or message)
    if "selected roles were not found" in lower:
        count = re.search(r"\b(\d+)\b", text)
        prefix = f"{count.group(1)} selected roles" if count else "Some selected roles"
        return _tag(
            "workable_selected_roles_missing",
            f"{prefix} were not found in Workable. Refresh the role list and retry.",
        )
    if "no workable jobs matched" in lower:
        return _tag(
            "workable_no_jobs_matched",
            "No Workable jobs matched the selected roles. Refresh the role list and retry.",
        )
    if "returned 0 jobs" in lower:
        return _tag(
            "workable_no_jobs_available",
            "Workable returned no jobs. Verify the r_jobs scope and that the account has jobs.",
        )
    if "paused mid-role" in lower:
        return _tag(
            "workable_sync_paused",
            "Paused mid-role for a pending Workable write; remaining candidates will retry on the next sync.",
        )
    if "pending workable write" in lower or "pending workable update" in lower:
        return _tag("workable_sync_paused")
    if "cancel" in lower and "user" in lower:
        return _tag("workable_sync_cancelled")
    if "stuck" in lower or "stale" in lower:
        return _tag("workable_sync_stale")
    if "worker failed" in lower or "worker stopped" in lower:
        return _tag("workable_sync_worker_failed")
    if "rate limit" in lower or "429" in lower:
        return _tag("workable_rate_limited")
    if any(token in lower for token in ("unauthor", "forbidden", "401", "403")):
        return _tag("workable_authorization_failed")
    if "404" in lower or "not found" in lower:
        return _tag("workable_not_found")
    if any(token in lower for token in ("timeout", "timed out", "network", "connect")):
        return _tag("workable_network_error")
    if any(token in lower for token in ("500", "502", "503", "504", "service unavailable")):
        return _tag("workable_unavailable")
    return _tag("workable_sync_failed")


def public_workable_sync_errors(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, BaseException)):
        values = [values]
    return [public_workable_sync_error(value) for value in (values or [])]


def public_workable_sync_summary(value: Any) -> dict:
    """Sanitize the error list in a stored sync summary/progress payload."""

    summary = dict(value or {}) if isinstance(value, dict) else {}
    summary["errors"] = public_workable_sync_errors(summary.get("errors"))
    return summary


__all__ = [
    "public_workable_exception",
    "public_workable_sync_error",
    "public_workable_sync_errors",
    "public_workable_sync_summary",
]
