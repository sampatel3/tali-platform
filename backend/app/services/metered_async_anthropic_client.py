"""Async sister of ``metered_anthropic_client.MeteredAnthropicClient``.
Graphiti attribution arrives via ``graph_metering_ctx``; only ``messages.create`` bills.
Invalid organization context and every other paid surface fail closed.
"""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Optional

from anthropic import AsyncAnthropic

from ..platform.database import SessionLocal
from .async_anthropic_call_log import (
    anthropic_usage_event_payload,
    record_async_anthropic_call_log,
)
from .claude_model_pricing import require_priceable_claude_model
from .anthropic_surface_guard import (
    NONBILLABLE_MESSAGE_OPERATIONS,
    NONBILLABLE_MODEL_OPERATIONS,
    NonbillableAnthropicResource,
    UnsupportedAnthropicSurfaceError,
)
from .anthropic_request_admission import anthropic_request_credit_upper_bound
from .pricing_service import Feature
from . import provider_retry_policy as retry_policy
from .provider_request_identity import provider_request_sha256
from .provider_usage_admission import (
    AutomaticProviderAuthorityError,
    mark_provider_attempt_started,
    mark_provider_usage_succeeded,
    release_provider_usage,
    release_provider_usage_if_definitely_nonbillable,
    reserve_provider_usage,
)
from .usage_credit_reservations import CreditReservation
from .usage_metering_service import record_event

logger = logging.getLogger("taali.metered_async_anthropic")


@dataclass(frozen=True)
class GraphMeteringContext:
    """Context-local attribution for one Graphiti provider operation."""

    organization_id: int
    role_id: Optional[int] = None
    candidate_id: Optional[int] = None
    user_id: Optional[int] = None
    episode_name: Optional[str] = None
    trace_id: Optional[str] = None
    require_hard_admission: bool = False
    require_role_admission: bool = False
    provider_attempt_callback: Callable[[], bool] | None = None


class GraphProviderAdmissionError(RuntimeError):
    """A billed graph call is missing the role needed for hard admission."""


class GraphUsageMeteringError(RuntimeError):
    """The provider returned a billable result that could not be metered."""


graph_metering_ctx: ContextVar[Optional[GraphMeteringContext]] = ContextVar(
    "graph_metering_ctx", default=None
)


def require_graph_outbox_provider_attempt_marker(
    ctx: GraphMeteringContext | None,
    reservation: CreditReservation | None,
    *,
    provider: str,
) -> None:
    """Fence a durable graph operation at the last point before the SDK."""

    callback = getattr(ctx, "provider_attempt_callback", None)
    if callback is None:
        return
    try:
        marked = bool(callback())
    except Exception:
        marked = False
    if marked:
        return
    release_provider_usage(
        reservation,
        reason=f"graphiti_{provider}_outbox_attempt_marker_failed",
        allow_started=True,
    )
    raise GraphProviderAdmissionError(
        f"could not durably mark {provider} graph-ingest attempt"
    )


class _AsyncMeteredMessages:
    """Meter async create; fail closed on unsupported paid surfaces."""

    def __init__(self, *, inner: Any):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in NONBILLABLE_MESSAGE_OPERATIONS:
            return getattr(self._inner, name)
        raise UnsupportedAnthropicSurfaceError(
            "Anthropic messages operation is unavailable until metering is implemented"
        )

    async def create(self, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "")
        require_priceable_claude_model(model)
        ctx = graph_metering_ctx.get()
        if ctx is None:
            raise GraphProviderAdmissionError(
                "Graphiti Anthropic call requires organization metering context"
            )
        if type(ctx.organization_id) is not int or ctx.organization_id <= 0:
            raise GraphProviderAdmissionError(
                "Graphiti Anthropic call requires positive organization attribution"
            )
        if ctx.role_id is not None and (
            type(ctx.role_id) is not int or ctx.role_id <= 0
        ):
            raise GraphProviderAdmissionError(
                "Graphiti role attribution must be a positive integer"
            )
        if any(
            value is not None and (type(value) is not int or value <= 0)
            for value in (ctx.user_id, ctx.candidate_id)
        ):
            raise GraphProviderAdmissionError(
                "Graphiti user/candidate attribution must be a positive integer"
            )
        if ctx.require_role_admission and ctx.role_id is None:
            raise GraphProviderAdmissionError(
                "hard-admitted Graphiti call requires role attribution"
            )
        required_amount = anthropic_request_credit_upper_bound(
            kwargs,
            feature=Feature.GRAPH_SYNC,
        )
        try:
            request_hash = provider_request_sha256(kwargs)
        except ValueError as exc:
            raise GraphProviderAdmissionError(str(exc)) from exc
        attempt_index = 0
        parent_call_log_id: int | None = None
        while True:
            reservation: CreditReservation | None = None
            try:
                reservation = reserve_provider_usage(
                    organization_id=int(ctx.organization_id),
                    role_id=int(ctx.role_id) if ctx.role_id is not None else None,
                    feature=Feature.GRAPH_SYNC,
                    trace_id=ctx.trace_id or ctx.episode_name or "graphiti-anthropic",
                    entity_id=(
                        str(ctx.candidate_id)
                        if ctx.candidate_id is not None
                        else None
                    ),
                    user_id=ctx.user_id,
                    candidate_id=ctx.candidate_id,
                    provider="anthropic",
                    model=model,
                    request_sha256=request_hash,
                    sub_feature="graphiti_anthropic",
                    amount=required_amount,
                    metadata={
                        "provider": "anthropic",
                        "model": model,
                        "episode_name": ctx.episode_name,
                    },
                    require_role_authority=bool(ctx.require_role_admission),
                )
            except AutomaticProviderAuthorityError as exc:
                raise GraphProviderAdmissionError(str(exc)) from exc
            if not mark_provider_attempt_started(reservation, provider="anthropic"):
                release_provider_usage(
                    reservation,
                    reason="graphiti_anthropic_attempt_marker_failed",
                )
                raise GraphProviderAdmissionError(
                    "could not durably mark Anthropic provider attempt"
                )
            require_graph_outbox_provider_attempt_marker(
                ctx,
                reservation,
                provider="anthropic",
            )
            try:
                response = await self._inner.create(**kwargs)
                break
            except asyncio.CancelledError as exc:
                logger.error(
                    "Graphiti Anthropic create cancelled model=%s error_type=%s",
                    model,
                    type(exc).__name__,
                )
                self._record_call_log_safe(
                    model=model,
                    usage=None,
                    status="sdk_ambiguous_error",
                    anthropic_request_id=None,
                    error=exc,
                    retry_attempt=attempt_index,
                    parent_call_log_id=parent_call_log_id,
                )
                raise
            except Exception as exc:
                released = release_provider_usage_if_definitely_nonbillable(
                    reservation,
                    error=exc,
                    reason="graphiti_anthropic_provider_error",
                )
                logger.error(
                    "Graphiti Anthropic create failed model=%s error_type=%s",
                    model,
                    type(exc).__name__,
                )
                failure_log_id = self._record_call_log_safe(
                    model=model,
                    usage=None,
                    status=(
                        "sdk_ambiguous_error"
                        if reservation is not None and not released
                        else "sdk_error"
                    ),
                    anthropic_request_id=None,
                    error=exc,
                    retry_attempt=attempt_index,
                    parent_call_log_id=parent_call_log_id,
                )
                if not retry_policy.should_retry_provider_error(
                    exc,
                    attempt_index=attempt_index,
                ):
                    raise
                if failure_log_id is None:
                    logger.error(
                        "Graphiti Anthropic retry blocked: failure evidence unavailable"
                    )
                    raise
                parent_call_log_id = failure_log_id
                attempt_index += 1
                await retry_policy.async_sleep_before_retry(
                    next_attempt_index=attempt_index,
                    error=exc,
                )

        usage = getattr(response, "usage", None)
        request_id = _extract_request_id(response)
        provider_receipt_recorded = False
        if reservation is not None:
            provider_receipt_recorded = mark_provider_usage_succeeded(
                reservation,
                deferred_usage_event=(
                    anthropic_usage_event_payload(
                        ctx,
                        usage=usage,
                        model=model,
                        request_sha256=request_hash,
                    )
                    if ctx is not None and usage is not None
                    else None
                ),
                provider="anthropic",
                provider_request_id=request_id,
            )

        usage_event_id: int | None = None
        settlement_error: Exception | None = None
        try:
            if reservation is not None and usage is None:
                raise GraphUsageMeteringError(
                    "Anthropic response did not include usage for hard-admitted call"
                )
            usage_event_id = self._write_usage_event_safe(
                usage=usage,
                model=model,
                request_sha256=request_hash,
                credit_reservation=reservation,
                strict=reservation is not None,
            )
        except Exception as exc:
            settlement_error = exc
            self._record_call_log_safe(
                model=model,
                usage=usage,
                status=(
                    "no_usage_on_response" if usage is None else "metering_error"
                ),
                anthropic_request_id=request_id,
                retry_attempt=attempt_index,
                parent_call_log_id=parent_call_log_id,
            )
            if provider_receipt_recorded:
                # A durable receipt makes replay costlier than deferred recovery.
                logger.error(
                    "Graphiti Anthropic immediate settlement deferred "
                    "model=%s usage_present=%s error_type=%s",
                    model,
                    usage is not None,
                    type(exc).__name__,
                )
                return response
        if settlement_error is not None:
            # No durable provider-success receipt exists. Preserve the funded
            # attempt marker and fail closed rather than pretending settlement
            # is recoverable.
            raise settlement_error
        self._record_call_log_safe(
            model=model,
            usage=usage,
            status="ok" if usage is not None else "no_usage_on_response",
            anthropic_request_id=request_id,
            usage_event_id=usage_event_id,
            retry_attempt=attempt_index,
            parent_call_log_id=parent_call_log_id,
        )
        return response

    def stream(self, **kwargs: Any) -> Any:
        """Fail closed until async stream settlement is implemented."""

        require_priceable_claude_model(str(kwargs.get("model") or ""))
        raise UnsupportedAnthropicSurfaceError(
            "Async Anthropic streaming is unavailable until metering is implemented"
        )

    def _record_call_log_safe(
        self,
        *,
        model: str,
        usage: Any,
        status: str,
        anthropic_request_id: Optional[str],
        usage_event_id: Optional[int] = None,
        error: BaseException | None = None,
        retry_attempt: int = 0,
        parent_call_log_id: int | None = None,
    ) -> int | None:
        """Write a secret-safe reconciliation row without raising."""
        ctx = graph_metering_ctx.get()
        org_id = ctx.organization_id if ctx is not None else None
        return record_async_anthropic_call_log(
            organization_id=org_id,
            model=model,
            usage=usage,
            status=status,
            anthropic_request_id=anthropic_request_id,
            usage_event_id=usage_event_id,
            error=error,
            retry_attempt=retry_attempt,
            parent_call_log_id=parent_call_log_id,
            trace_id=(
                str(ctx.trace_id or ctx.episode_name)
                if ctx is not None and (ctx.trace_id or ctx.episode_name)
                else None
            ),
        )

    def _write_usage_event_safe(
        self,
        *,
        usage: Any,
        model: str,
        request_sha256: str,
        credit_reservation: CreditReservation | None = None,
        strict: bool = False,
    ) -> Optional[int]:
        """Write a ``UsageEvent`` row when org context is set on the
        contextvar. Returns the row id so the call_log can link to it.
        """
        if usage is None:
            return None
        ctx = graph_metering_ctx.get()
        if ctx is None:
            # No org context → can't bill. Call_log will still record
            # the spend so reconciliation closes; only the customer-
            # facing usage tab misses the row.
            logger.warning(
                "metered_async_anthropic: graph_metering_ctx unset for "
                "model=%s call — claude_call_log written but no usage_event "
                "(no org attribution). Set graph_metering_ctx before "
                "graphiti.add_episode to fix.",
                model,
            )
            return None

        payload = anthropic_usage_event_payload(
            ctx,
            usage=usage,
            model=model,
            request_sha256=request_sha256,
        )
        try:
            with SessionLocal() as fresh:
                event = record_event(
                    fresh,
                    **{
                        key: value
                        for key, value in payload.items()
                        if key not in {"candidate_id", "provider", "request_sha256"}
                    },
                    credit_reservation=(
                        credit_reservation.as_metering_payload()
                        if credit_reservation is not None
                        else None
                    ),
                )
                fresh.commit()
                fresh.refresh(event)
                return int(event.id)
        except Exception as exc:
            logger.error(
                "metered_async_anthropic: usage_event write failed for "
                "org=%s model=%s error_type=%s",
                ctx.organization_id,
                model,
                type(exc).__name__,
            )
            if strict:
                raise GraphUsageMeteringError(
                    "Anthropic usage settlement failed for hard-admitted "
                    f"Graphiti call (org={ctx.organization_id}, model={model})"
                ) from exc
            return None


class MeteredAsyncAnthropic:
    """Narrow, metered ``AsyncAnthropic`` adapter for Graphiti."""

    def __init__(self, *, inner: AsyncAnthropic):
        retry_policy.require_sdk_retries_disabled(inner, provider="Anthropic")
        self._inner = inner
        self._messages = _AsyncMeteredMessages(inner=inner.messages)

    @property
    def messages(self) -> _AsyncMeteredMessages:
        return self._messages

    @property
    def models(self) -> NonbillableAnthropicResource:
        return NonbillableAnthropicResource(
            inner=self._inner.models,
            allowed_operations=NONBILLABLE_MODEL_OPERATIONS,
        )

    @property
    def inner(self) -> AsyncAnthropic:
        raise UnsupportedAnthropicSurfaceError(
            "The bare Anthropic client is unavailable because it bypasses metering"
        )

    async def close(self) -> Any:
        """Close transport resources without exposing the provider client."""

        return await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise UnsupportedAnthropicSurfaceError(
            "Anthropic SDK surface is unavailable until metering is implemented"
        )


def _extract_request_id(response: Any) -> Optional[str]:
    for attr in ("id", "_request_id", "request_id"):
        val = getattr(response, attr, None)
        if isinstance(val, str) and val:
            return val
    return None
