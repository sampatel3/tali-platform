"""Reconciliation evidence for one async Anthropic graph call."""

from __future__ import annotations

import logging
from typing import Any

from ..models.claude_call_log import ClaudeCallLog
from ..platform.database import SessionLocal
from .anthropic_usage_tokens import (
    extract_cache_creation_1h as _extract_cache_creation_1h,
)
from .pricing_service import Feature, raw_cost_usd_micro
from .provider_error_evidence import (
    classify_anthropic_exception,
    safe_provider_error_code,
)

logger = logging.getLogger("taali.metered_async_anthropic")


def anthropic_usage_event_payload(
    ctx: Any,
    *,
    usage: Any,
    model: str,
    request_sha256: str,
) -> dict[str, Any]:
    """Build the canonical live/deferred receipt for one graph LLM call."""

    return {
        "organization_id": int(ctx.organization_id),
        "feature": Feature.GRAPH_SYNC.value,
        "model": str(model),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        ),
        "cache_creation_tokens": int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
        "cache_creation_1h_tokens": _extract_cache_creation_1h(usage),
        "user_id": int(ctx.user_id) if ctx.user_id is not None else None,
        "role_id": int(ctx.role_id) if ctx.role_id is not None else None,
        "entity_id": str(ctx.candidate_id) if ctx.candidate_id is not None else None,
        "candidate_id": int(ctx.candidate_id) if ctx.candidate_id is not None else None,
        "provider": "anthropic",
        "request_sha256": request_sha256,
        "metadata": {
            **({"episode_name": ctx.episode_name} if ctx.episode_name else {}),
            **({"trace_id": ctx.trace_id} if ctx.trace_id else {}),
            "provider": "anthropic",
            "candidate_id": (
                int(ctx.candidate_id) if ctx.candidate_id is not None else None
            ),
            "request_sha256": request_sha256,
        },
    }


def record_async_anthropic_call_log(
    *,
    organization_id: int | None,
    model: str,
    usage: Any,
    status: str,
    anthropic_request_id: str | None,
    usage_event_id: int | None,
    error: BaseException | None = None,
    retry_attempt: int = 0,
    trace_id: str | None = None,
    parent_call_log_id: int | None = None,
) -> int | None:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    cache_read_tokens = (
        int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
    )
    cache_creation_tokens = (
        int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
    )
    cache_creation_1h_tokens = _extract_cache_creation_1h(usage)
    try:
        cost_micro = raw_cost_usd_micro(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_creation_1h_tokens=cache_creation_1h_tokens,
            model=model,
        )
    except Exception:
        cost_micro = 0
    error_class, http_status = (
        classify_anthropic_exception(error) if error is not None else (None, None)
    )
    row = ClaudeCallLog(
        organization_id=organization_id,
        model=model or "(unknown)",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_creation_1h_tokens=cache_creation_1h_tokens,
        cost_usd_micro=int(cost_micro),
        feature_hint="graph_sync",
        status=status,
        anthropic_request_id=anthropic_request_id,
        usage_event_id=usage_event_id,
        error_reason=(
            safe_provider_error_code(error, operation="anthropic_create")
            if error is not None
            else None
        ),
        error_class=error_class,
        http_status=http_status,
        retry_attempt=retry_attempt,
        trace_id=trace_id,
        parent_call_log_id=parent_call_log_id,
    )
    try:
        with SessionLocal() as session:
            session.add(row)
            session.flush()
            row_id = int(row.id)
            session.commit()
        return row_id
    except Exception as exc:
        logger.error(
            "metered_async_anthropic: claude_call_log write failed "
            "(model=%s status=%s error_type=%s)",
            model,
            status,
            type(exc).__name__,
        )
        return None


__all__ = ["anthropic_usage_event_payload", "record_async_anthropic_call_log"]
