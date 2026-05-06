"""Anthropic Admin API client for usage and cost reports.

Used by ``anthropic_reconciliation_service`` to pull authoritative
per-day, per-workspace, per-model token counts and costs from
Anthropic's billing system. The platform stores per-call telemetry in
``usage_events``; this module is the source of truth we reconcile
against.

API reference (verified 2026-05):
- GET /v1/organizations/usage_report/messages
- GET /v1/organizations/cost_report

Both endpoints support pagination via ``has_more`` / ``next_page`` and
require the ``x-api-key`` admin key plus ``anthropic-version: 2023-06-01``.
Data freshness is ~5 minutes per Anthropic; we run reconciliation on a
daily schedule with a 24h+ lag to leave room for late-arriving rows.

This module is read-only — it never mutates Anthropic state. Failures
are surfaced as ``AnthropicUsageError`` so the reconciliation task can
log + retry without taking the platform down.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

import httpx

from ....platform.config import settings

logger = logging.getLogger("taali.anthropic_usage_reports")


_ADMIN_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_HTTP_TIMEOUT_SECONDS = 30.0
_MAX_PAGES = 100  # safety net — daily reports never approach this


class AnthropicUsageError(Exception):
    """Any failure interacting with the usage / cost report endpoints."""


@dataclass(frozen=True)
class UsageBucket:
    """One row from the usage report — pre-flattened for our schema.

    Anthropic returns nested ``cache_creation`` (5m + 1h ephemeral) and
    server tool counts; we sum the cache buckets so reconciliation
    matches our internal ``cache_creation_tokens`` (we don't yet split
    5m vs 1h — when we do, this struct grows).
    """

    starting_at: datetime
    ending_at: datetime
    workspace_id: Optional[str]      # None = default workspace
    api_key_id: Optional[str]
    model: Optional[str]
    service_tier: Optional[str]
    context_window: Optional[str]
    uncached_input_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int  # sum of 5m + 1h ephemeral
    output_tokens: int
    web_search_requests: int


@dataclass(frozen=True)
class CostBucket:
    """One row from the cost report.

    ``amount_cents`` is the decimal string from Anthropic converted to a
    whole-cent integer (Anthropic returns it as a decimal string in cents).
    Multiply by 10_000 to get micro-USD if needed downstream.
    """

    starting_at: datetime
    ending_at: datetime
    workspace_id: Optional[str]
    cost_type: Optional[str]          # tokens | web_search | code_execution | session_usage
    description: Optional[str]
    model: Optional[str]
    token_type: Optional[str]
    context_window: Optional[str]
    service_tier: Optional[str]
    amount_cents: int                 # whole cents (rounded from decimal)
    currency: str                     # "USD"


def _admin_headers() -> dict[str, str]:
    admin_key = (settings.ANTHROPIC_ADMIN_API_KEY or "").strip()
    if not admin_key:
        raise AnthropicUsageError("ANTHROPIC_ADMIN_API_KEY is not configured")
    return {
        "x-api-key": admin_key,
        "anthropic-version": _ANTHROPIC_VERSION,
    }


def is_configured() -> bool:
    return bool((settings.ANTHROPIC_ADMIN_API_KEY or "").strip())


def _parse_dt(raw: str) -> datetime:
    # RFC 3339 timestamps from Anthropic always end in "Z" or include offset.
    # Python's fromisoformat accepts the offset form; we coerce "Z" → "+00:00".
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def _decimal_string_to_cents(raw: str) -> int:
    """Anthropic returns amounts as decimal strings in lowest currency
    units (cents). E.g. ``"123.45"`` is $1.2345. We round half-up to a
    whole cent — close enough for reconciliation totals (we display
    sub-cent precision separately when comparing internal vs external)."""
    try:
        from decimal import Decimal, ROUND_HALF_UP

        d = Decimal(raw or "0")
        return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:  # pragma: no cover — defensive
        return 0


def fetch_usage_buckets(
    *,
    starting_at: datetime,
    ending_at: datetime,
    bucket_width: str = "1d",
    workspace_ids: Optional[list[str]] = None,
    models: Optional[list[str]] = None,
) -> Iterator[UsageBucket]:
    """Stream usage rows from ``/v1/organizations/usage_report/messages``.

    Always groups by ``workspace_id`` AND ``model`` so each bucket is
    directly comparable to a slice of our internal ``usage_events``.
    Yields ``UsageBucket`` per result entry — pagination is handled
    transparently.

    Raises ``AnthropicUsageError`` on HTTP errors. Caller logs and skips
    that day's reconciliation; the next run picks it up from the
    ``last_reconciled_at`` watermark.
    """
    headers = _admin_headers()
    params: dict = {
        "starting_at": _to_iso_z(starting_at),
        "ending_at": _to_iso_z(ending_at),
        "bucket_width": bucket_width,
        # Repeated keys → multi-value params; httpx serializes correctly.
        "group_by[]": ["workspace_id", "model"],
        # Generous limit so we don't paginate within a day; max for 1d is 31.
        "limit": 31,
    }
    if workspace_ids:
        params["workspace_ids[]"] = list(workspace_ids)
    if models:
        params["models[]"] = list(models)

    yield from _paginate(
        path="/v1/organizations/usage_report/messages",
        params=params,
        headers=headers,
        parse_bucket=_parse_usage_bucket,
    )


def fetch_cost_buckets(
    *,
    starting_at: datetime,
    ending_at: datetime,
    workspace_ids: Optional[list[str]] = None,
) -> Iterator[CostBucket]:
    """Stream cost rows from ``/v1/organizations/cost_report``.

    Always groups by ``workspace_id`` AND ``description`` — the latter
    splits the row by cost_type/model/token_type so we can match
    internal cost lines exactly.
    """
    headers = _admin_headers()
    params: dict = {
        "starting_at": _to_iso_z(starting_at),
        "ending_at": _to_iso_z(ending_at),
        "bucket_width": "1d",  # cost endpoint only supports 1d
        "group_by[]": ["workspace_id", "description"],
        "limit": 31,
    }
    if workspace_ids:
        params["workspace_ids[]"] = list(workspace_ids)

    yield from _paginate(
        path="/v1/organizations/cost_report",
        params=params,
        headers=headers,
        parse_bucket=_parse_cost_bucket,
    )


# ---- internals -------------------------------------------------------------


def _to_iso_z(dt: datetime) -> str:
    """RFC 3339 with trailing Z — Anthropic snaps to UTC bucket boundaries
    so passing UTC naïvely is fine, but we coerce to UTC explicitly."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paginate(
    *,
    path: str,
    params: dict,
    headers: dict,
    parse_bucket,
) -> Iterator:
    next_page: Optional[str] = None
    pages = 0
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        while pages < _MAX_PAGES:
            page_params = dict(params)
            if next_page:
                page_params["page"] = next_page
            response = client.get(
                f"{_ADMIN_BASE_URL}{path}",
                headers=headers,
                params=page_params,
            )
            if response.status_code >= 400:
                raise AnthropicUsageError(
                    f"{path} failed: {response.status_code} {response.text[:300]}"
                )
            payload = response.json()

            for time_bucket in payload.get("data") or []:
                starting_at = _parse_dt(time_bucket["starting_at"])
                ending_at = _parse_dt(time_bucket["ending_at"])
                for entry in time_bucket.get("results") or []:
                    yield parse_bucket(
                        starting_at=starting_at,
                        ending_at=ending_at,
                        entry=entry,
                    )

            if not payload.get("has_more"):
                return
            next_page = payload.get("next_page")
            if not next_page:
                return
            pages += 1


def _parse_usage_bucket(*, starting_at, ending_at, entry: dict) -> UsageBucket:
    cache_creation = entry.get("cache_creation") or {}
    cache_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
    cache_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
    server_tool = entry.get("server_tool_use") or {}
    return UsageBucket(
        starting_at=starting_at,
        ending_at=ending_at,
        workspace_id=entry.get("workspace_id"),
        api_key_id=entry.get("api_key_id"),
        model=entry.get("model"),
        service_tier=entry.get("service_tier"),
        context_window=entry.get("context_window"),
        uncached_input_tokens=int(entry.get("uncached_input_tokens") or 0),
        cache_read_input_tokens=int(entry.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=cache_5m + cache_1h,
        output_tokens=int(entry.get("output_tokens") or 0),
        web_search_requests=int(server_tool.get("web_search_requests") or 0),
    )


def _parse_cost_bucket(*, starting_at, ending_at, entry: dict) -> CostBucket:
    return CostBucket(
        starting_at=starting_at,
        ending_at=ending_at,
        workspace_id=entry.get("workspace_id"),
        cost_type=entry.get("cost_type"),
        description=entry.get("description"),
        model=entry.get("model"),
        token_type=entry.get("token_type"),
        context_window=entry.get("context_window"),
        service_tier=entry.get("service_tier"),
        amount_cents=_decimal_string_to_cents(str(entry.get("amount") or "0")),
        currency=str(entry.get("currency") or "USD"),
    )
