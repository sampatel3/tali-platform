"""Immutable plan and recovery-contract validation for scoring backfills."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ..models.background_job_run import BackgroundJobRun


SCORING_BACKFILL_PLAN_VERSION = 1
_MAX_FUTURE_LEASE_SECONDS = 15 * 60


def positive_int(value: object) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


def target_ids(value: object) -> list[int] | None:
    if not isinstance(value, list):
        return None
    normalized = sorted({item for item in value if type(item) is int and item > 0})
    if not normalized or len(normalized) != len(value):
        return None
    return normalized


def parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def normalize_scoring_backfill_plan(value: object) -> list[dict[str, Any]] | None:
    """Validate and canonicalize one immutable role/target snapshot."""

    if not isinstance(value, list):
        return None
    normalized: list[dict[str, Any]] = []
    seen_roles: set[int] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            return None
        role_id = positive_int(raw.get("role_id"))
        targets = target_ids(raw.get("target_application_ids"))
        if role_id is None or targets is None or role_id in seen_roles:
            return None
        seen_roles.add(role_id)
        normalized.append(
            {
                "role_id": role_id,
                "role_name": str(raw.get("role_name") or ""),
                "target_application_ids": targets,
            }
        )
    if [entry["role_id"] for entry in normalized] != sorted(seen_roles):
        return None
    return normalized


def scoring_backfill_plan_digest(plan: list[dict[str, Any]]) -> str:
    """Return a stable integrity receipt for the immutable target plan."""

    encoded = json.dumps(
        plan,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scoring_backfill_plan_from_counters(
    counters: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    if counters.get("role_plan_version") != SCORING_BACKFILL_PLAN_VERSION:
        return None
    plan = normalize_scoring_backfill_plan(counters.get("role_plan"))
    if plan is None:
        return None
    digest = counters.get("role_plan_digest")
    if not isinstance(digest, str) or digest != scoring_backfill_plan_digest(plan):
        return None
    return plan


def backfill_contract_error(
    parent: BackgroundJobRun,
    *,
    now: datetime | None = None,
) -> str | None:
    """Validate an active org-scoped recovery receipt before marker filtering."""

    if not isinstance(parent.counters, dict):
        return "counters_not_object"
    counters = parent.counters
    if int(parent.scope_id) != int(parent.organization_id):
        return "invalid_parent_scope"
    if counters.get("backfill_parent") is not True:
        return "invalid_backfill_parent"
    if type(counters.get("include_scored")) is not bool:
        return "invalid_include_scored"
    if type(counters.get("fanout_complete")) is not bool:
        return "invalid_fanout_complete"
    if scoring_backfill_plan_from_counters(counters) is None:
        return "invalid_role_plan"
    raw_lease = counters.get("fanout_lease_expires_at")
    if raw_lease is None:
        return None
    lease = parsed_datetime(raw_lease)
    if lease is None:
        return "invalid_fanout_lease_expires_at"
    current = now or datetime.now(timezone.utc)
    if lease > current + timedelta(seconds=_MAX_FUTURE_LEASE_SECONDS):
        return "invalid_future_fanout_lease_expires_at"
    return None


def backfill_contract_error_code(reason: str) -> str:
    if reason in {
        "invalid_fanout_lease_expires_at",
        "invalid_future_fanout_lease_expires_at",
    }:
        return "scoring_backfill_fanout_lease_invalid"
    if reason == "invalid_role_plan":
        return "scoring_backfill_plan_invalid"
    return "scoring_backfill_recovery_contract_invalid"


__all__ = [
    "SCORING_BACKFILL_PLAN_VERSION",
    "backfill_contract_error",
    "backfill_contract_error_code",
    "normalize_scoring_backfill_plan",
    "parsed_datetime",
    "positive_int",
    "scoring_backfill_plan_digest",
    "scoring_backfill_plan_from_counters",
    "target_ids",
]
