"""Timestamp normalization shared by ATS stuck-run watchdogs."""

from __future__ import annotations

from datetime import datetime, timezone


def ats_attempt_started_at(run) -> datetime | None:
    value = (
        (run.counters or {}).get("last_started_at")
        if run.status == "running"
        else None
    ) or run.started_at
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


__all__ = ["ats_attempt_started_at"]
