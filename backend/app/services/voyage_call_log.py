"""Secret-safe call-log evidence for async Voyage attempts."""

from __future__ import annotations

import logging

from ..models.claude_call_log import ClaudeCallLog
from ..platform.database import SessionLocal
from .metered_async_anthropic_client import graph_metering_ctx
from .provider_error_evidence import safe_provider_error_code

logger = logging.getLogger("taali.metered_voyage")


def record_voyage_failure_evidence(
    *,
    model: str,
    error: BaseException,
    status: str,
    retry_attempt: int,
    parent_call_log_id: int | None = None,
) -> int | None:
    """Persist one zero-usage failed/ambiguous wire-attempt row."""

    ctx = graph_metering_ctx.get()
    logger.error(
        "Voyage embed failed model=%s error_type=%s",
        model,
        type(error).__name__,
    )
    try:
        with SessionLocal() as session:
            row = ClaudeCallLog(
                organization_id=(
                    int(ctx.organization_id) if ctx is not None else None
                ),
                model=model or "voyage-3",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost_usd_micro=0,
                feature_hint="graph_sync",
                status=status,
                error_reason=safe_provider_error_code(
                    error,
                    operation="voyage_embed",
                ),
                trace_id=(
                    str(ctx.trace_id or ctx.episode_name)
                    if ctx is not None and (ctx.trace_id or ctx.episode_name)
                    else None
                ),
                retry_attempt=retry_attempt,
                parent_call_log_id=parent_call_log_id,
            )
            session.add(row)
            session.flush()
            row_id = int(row.id)
            session.commit()
        return row_id
    except Exception as exc:
        logger.warning(
            "metered_voyage: provider failure evidence write failed error_type=%s",
            type(exc).__name__,
        )
        return None


__all__ = ["record_voyage_failure_evidence"]
