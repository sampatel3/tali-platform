"""Content-free provider evidence extraction for routing attempts."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.claude_call_log import ClaudeCallLog
from .model_registry import ModelDeployment
from .pricing import RoutedPricingReceiptError, routed_cost_usd_micro

logger = logging.getLogger("taali.ai_routing.attempt_evidence")

_KNOWN_USAGE_STATUSES = frozenset(
    {
        "ok",
        "interrupted",
        # Legacy rows retain the original undifferentiated status.
        "metering_error",
        "metering_error_completed",
        "metering_error_interrupted",
        # The response violated the route, but its actual registered model,
        # region, token counts, and exact registry price were persisted before
        # the contract error propagated.
        "routed_contract_mismatch",
    }
)


def provider_evidence(
    session_factory: Callable[[], Session], trace_id: str
) -> ClaudeCallLog | None:
    """Return the independently committed Anthropic call-log row, if present."""

    try:
        with session_factory() as session:
            row = session.scalar(
                select(ClaudeCallLog)
                .where(ClaudeCallLog.trace_id == trace_id)
                .order_by(ClaudeCallLog.id.desc())
                .limit(1)
            )
            if row is None:
                return None
            session.expunge(row)
            return row
    except Exception:
        logger.exception("could not link provider evidence trace_id=%s", trace_id)
        return None


def latency_ms(started_monotonic: float) -> int:
    return max(0, int((monotonic() - started_monotonic) * 1000))


def evidence_has_known_usage(evidence: ClaudeCallLog) -> bool:
    """Whether the provider log was written from an actual usage object."""

    return str(evidence.status) in _KNOWN_USAGE_STATUSES


def evidence_usage_values(evidence: ClaudeCallLog) -> dict[str, int]:
    """Project the authoritative provider log into generic attempt fields."""

    return {
        "input_tokens": int(evidence.input_tokens or 0),
        "output_tokens": int(evidence.output_tokens or 0),
        "cache_read_tokens": int(evidence.cache_read_tokens or 0),
        "cache_creation_tokens": int(evidence.cache_creation_tokens or 0),
        "cost_usd_micro": int(evidence.cost_usd_micro or 0),
    }


def response_request_id(response: Any) -> str | None:
    for attribute in ("id", "_request_id"):
        value = getattr(response, attribute, None)
        if value:
            return str(value)
    return None


def exception_request_id(error: BaseException) -> str | None:
    for source in (error, getattr(error, "response", None)):
        if source is None:
            continue
        for attribute in ("request_id", "_request_id", "id"):
            value = getattr(source, attribute, None)
            if value:
                return str(value)
    return None


def status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def error_class(
    *, provider_status: int | None, error: BaseException, ambiguous: bool
) -> str:
    if ambiguous:
        if provider_status is not None and provider_status >= 500:
            return "provider.server_error_ambiguous.v1"
        name = type(error).__name__.lower()
        if "timeout" in name:
            return "provider.timeout_ambiguous.v1"
        if "connection" in name or "network" in name:
            return "provider.network_ambiguous.v1"
        return "provider.outcome_ambiguous.v1"
    if provider_status == 429:
        return "provider.rate_limited.v1"
    if provider_status == 404:
        return "provider.deployment_unavailable.v1"
    if provider_status in {401, 403}:
        return "provider.authorization_rejected.v1"
    return f"provider.request_rejected_{provider_status or 400}.v1"


def usage_values(
    *,
    usage: Any,
    deployment: ModelDeployment,
    region: str = "global",
    service_tier: str = "standard",
) -> dict[str, int]:
    """Normalize Anthropic usage and price it from the deployment registry."""

    input_tokens = _exact_usage_int(usage, "input_tokens")
    output_tokens = _exact_usage_int(usage, "output_tokens")
    cache_read_tokens = _exact_usage_int(usage, "cache_read_input_tokens")
    cache_creation_tokens = _exact_usage_int(usage, "cache_creation_input_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cost_usd_micro": routed_cost_usd_micro(
            usage=usage,
            deployment=deployment,
            region=region,
            service_tier=service_tier,
        ),
    }


def _exact_usage_int(usage: Any, field: str) -> int:
    value = getattr(usage, field, 0)
    if value is None:
        value = 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoutedPricingReceiptError(
            f"Anthropic usage field {field!r} is not an exact integer"
        )
    if value < 0:
        raise RoutedPricingReceiptError(
            f"Anthropic usage field {field!r} cannot be negative"
        )
    return value


__all__ = [
    "evidence_has_known_usage",
    "evidence_usage_values",
    "error_class",
    "exception_request_id",
    "latency_ms",
    "provider_evidence",
    "response_request_id",
    "status_code",
    "usage_values",
]
