"""Metering wire-tap for Voyage AI embedding calls (Graphiti's vector layer).

Anthropic has no embeddings API; Graphiti uses Voyage for the knowledge-graph
embeddings (the LLM entity/edge extraction stays on Anthropic/Haiku and is
metered by ``metered_async_anthropic_client``). Voyage spend was previously
invisible to billing + the org budget.

This wraps the Voyage async client's ``embed`` so every call books a
``usage_event`` (feature=graph_sync, model="voyage-*") + a ``claude_call_log``
row, attributed to the org via the SAME ``graph_metering_ctx`` the Anthropic
graph wrapper uses (propagated onto the Graphiti loop thread by
``client.run_async``'s ``copy_context``).

Voyage models never match an Anthropic model family, so these rows are
naturally excluded from the Anthropic Admin-API reconciliation; they price via
``pricing_service.voyage_cost_micro`` and flow into credits + the budget.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..models.claude_call_log import ClaudeCallLog
from ..platform.database import SessionLocal
from .metered_async_anthropic_client import (
    GraphProviderAdmissionError,
    GraphUsageMeteringError,
    graph_metering_ctx,
    require_graph_outbox_provider_attempt_marker,
)
from .pricing_service import (
    Feature,
    credits_charged,
    require_priceable_voyage_model,
    voyage_cost_micro,
)
from .provider_request_identity import provider_request_sha256
from . import provider_retry_policy as retry_policy
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
from .voyage_call_log import (
    record_voyage_failure_evidence as _record_voyage_failure_evidence,
)

logger = logging.getLogger("taali.metered_voyage")

_DEFAULT_VOYAGE_MODEL = "voyage-3"
_VOYAGE_INPUT_TYPE_PREFIX_BYTES = 64


class UnsupportedVoyageSurfaceError(RuntimeError):
    """A Voyage SDK operation has no reviewed metering implementation."""


def _request_model_and_reservation(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str, int]:
    if len(args) >= 2 and "model" in kwargs:
        raise TypeError("Voyage embed model was supplied more than once")
    model_value = kwargs.get("model")
    if model_value is None and len(args) >= 2:
        model_value = args[1]
    model = require_priceable_voyage_model(
        _DEFAULT_VOYAGE_MODEL if model_value is None else model_value
    )
    texts = kwargs.get("texts") if "texts" in kwargs else (args[0] if args else None)
    if isinstance(texts, str):
        text_items = [texts]
    elif isinstance(texts, (list, tuple)) and all(
        isinstance(item, str) for item in texts
    ):
        text_items = list(texts)
    else:
        raise ValueError("Voyage embed texts must be a finite string list")
    if not 1 <= len(text_items) <= 1_000:
        raise ValueError("Voyage embed requires between 1 and 1000 texts")

    input_type = kwargs.get("input_type")
    if input_type is None and len(args) >= 3:
        input_type = args[2]
    if input_type not in (None, "query", "document"):
        raise ValueError("unsupported Voyage embedding input_type")
    token_upper = sum(len(text.encode("utf-8")) for text in text_items)
    if input_type is not None:
        token_upper += _VOYAGE_INPUT_TYPE_PREFIX_BYTES * len(text_items)
    request_bound = credits_charged(
        feature=Feature.GRAPH_SYNC,
        cost_usd_micro=voyage_cost_micro(model=model, input_tokens=token_upper),
    )
    # UTF-8 bytes are a conservative upper bound for Voyage input tokens.  A
    # generic graph-sync estimate can be orders of magnitude larger than this
    # known request and needlessly exhaust org/role capacity under concurrency.
    return model, request_bound


def _require_optional_positive_int(value: Any, *, field: str) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise GraphProviderAdmissionError(
            f"Voyage {field} attribution must be a positive integer"
        )


class MeteredVoyageClient:
    """Narrow ``voyageai.AsyncClient`` adapter supporting metered ``embed``."""

    def __init__(self, inner: Any):
        try:
            retry_policy.require_sdk_retries_disabled(inner, provider="Voyage")
        except RuntimeError as exc:
            raise UnsupportedVoyageSurfaceError(
                "Voyage SDK retries must be disabled for per-attempt metering"
            ) from exc
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise UnsupportedVoyageSurfaceError(
            "Voyage SDK surface is unavailable until metering is implemented"
        )

    async def embed(self, *args: Any, **kwargs: Any) -> Any:
        model, required_amount = _request_model_and_reservation(args, kwargs)
        # The wrapper's priced default must also be the provider's actual
        # model. Voyage SDK defaults have changed across releases; forwarding
        # an omission would let execution and the reservation receipt diverge.
        if len(args) >= 2:
            if args[1] is None:
                args = (args[0], model, *args[2:])
        elif kwargs.get("model") is None:
            kwargs["model"] = model
        try:
            request_hash = provider_request_sha256(
                {"args": list(args), "kwargs": kwargs}
            )
        except ValueError as exc:
            raise GraphProviderAdmissionError(str(exc)) from exc
        ctx = graph_metering_ctx.get()
        if (
            ctx is None
            or type(ctx.organization_id) is not int
            or ctx.organization_id <= 0
        ):
            raise GraphProviderAdmissionError(
                "Voyage embed requires positive organization metering context"
            )
        _require_optional_positive_int(ctx.role_id, field="role")
        _require_optional_positive_int(ctx.user_id, field="user")
        _require_optional_positive_int(ctx.candidate_id, field="candidate")
        if ctx.require_role_admission and ctx.role_id is None:
            raise GraphProviderAdmissionError(
                "hard-admitted Graphiti call requires role attribution"
            )
        attempt_index = 0
        parent_call_log_id: int | None = None
        while True:
            try:
                reservation = reserve_provider_usage(
                    organization_id=int(ctx.organization_id),
                    role_id=int(ctx.role_id) if ctx.role_id is not None else None,
                    feature=Feature.GRAPH_SYNC,
                    trace_id=ctx.trace_id or ctx.episode_name or "graphiti-voyage",
                    entity_id=(
                        str(ctx.candidate_id)
                        if ctx.candidate_id is not None
                        else None
                    ),
                    user_id=ctx.user_id,
                    candidate_id=ctx.candidate_id,
                    provider="voyage",
                    model=model,
                    request_sha256=request_hash,
                    sub_feature="graphiti_voyage",
                    amount=required_amount,
                    metadata={
                        "provider": "voyage",
                        "model": model,
                        "episode_name": ctx.episode_name,
                    },
                    require_role_authority=bool(ctx.require_role_admission),
                )
            except AutomaticProviderAuthorityError as exc:
                raise GraphProviderAdmissionError(str(exc)) from exc
            if not mark_provider_attempt_started(reservation, provider="voyage"):
                release_provider_usage(
                    reservation,
                    reason="graphiti_voyage_attempt_marker_failed",
                )
                raise GraphProviderAdmissionError(
                    "could not durably mark Voyage provider attempt"
                )
            require_graph_outbox_provider_attempt_marker(
                ctx,
                reservation,
                provider="voyage",
            )
            try:
                result = await self._inner.embed(*args, **kwargs)
                break
            except asyncio.CancelledError as exc:
                _record_voyage_failure_evidence(
                    model=model,
                    error=exc,
                    status="sdk_ambiguous_error",
                    retry_attempt=attempt_index,
                    parent_call_log_id=parent_call_log_id,
                )
                raise
            except Exception as exc:
                released = release_provider_usage_if_definitely_nonbillable(
                    reservation,
                    error=exc,
                    reason="graphiti_voyage_provider_error",
                )
                failure_log_id = _record_voyage_failure_evidence(
                    model=model,
                    error=exc,
                    status=(
                        "sdk_ambiguous_error"
                        if reservation is not None and not released
                        else "sdk_error"
                    ),
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
                        "Voyage retry blocked: failure evidence unavailable"
                    )
                    raise
                parent_call_log_id = failure_log_id
                attempt_index += 1
                await retry_policy.async_sleep_before_retry(
                    next_attempt_index=attempt_index,
                    error=exc,
                )

        total_tokens = int(getattr(result, "total_tokens", 0) or 0)
        provider_receipt_recorded = False
        if reservation is not None:
            provider_receipt_recorded = mark_provider_usage_succeeded(
                reservation,
                deferred_usage_event=(
                    _voyage_usage_event_payload(
                        ctx,
                        model=model,
                        total_tokens=total_tokens,
                        request_sha256=request_hash,
                    )
                    if total_tokens > 0
                    else None
                ),
                provider="voyage",
            )
        if reservation is not None and total_tokens <= 0:
            metering_error = GraphUsageMeteringError(
                "Voyage response did not include token usage for hard-admitted call"
            )
            _record_voyage_failure_evidence(
                model=model,
                error=metering_error,
                status="no_usage_on_response",
                retry_attempt=attempt_index,
                parent_call_log_id=parent_call_log_id,
            )
            if provider_receipt_recorded:
                logger.error(
                    "Voyage usage unavailable after durable provider success model=%s",
                    model,
                )
                return result
            raise metering_error
        if total_tokens > 0:
            try:
                _record_voyage_usage(
                    model=model,
                    total_tokens=total_tokens,
                    request_sha256=request_hash,
                    credit_reservation=reservation,
                    strict=True,
                    retry_attempt=attempt_index,
                    parent_call_log_id=parent_call_log_id,
                )
            except GraphUsageMeteringError as exc:
                if not provider_receipt_recorded:
                    raise
                logger.error(
                    "Voyage immediate settlement deferred model=%s error_type=%s",
                    model,
                    type(exc).__name__,
                )
        return result


def _record_voyage_usage(
    *,
    model: str,
    total_tokens: int,
    request_sha256: str,
    credit_reservation: CreditReservation | None = None,
    strict: bool = False,
    retry_attempt: int = 0,
    parent_call_log_id: int | None = None,
) -> None:
    """Write a usage_event (when org context is set) + a call_log row for one
    Voyage embed call. Raises only for strict hard-admission settlement."""
    ctx = graph_metering_ctx.get()
    usage_event_id: Optional[int] = None
    usage_event_error: Exception | None = None

    if ctx is None:
        raise GraphUsageMeteringError("Voyage usage lost organization context")
    try:
        payload = _voyage_usage_event_payload(
            ctx,
            model=model,
            total_tokens=total_tokens,
            request_sha256=request_sha256,
        )
        with SessionLocal() as session:
            event = record_event(
                session,
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
            session.flush()
            usage_event_id = int(event.id)
            session.commit()
    except Exception as exc:
        logger.warning(
            "metered_voyage: usage_event write failed error_type=%s",
            type(exc).__name__,
        )
        usage_event_id = None
        usage_event_error = exc

    try:
        cost_micro = voyage_cost_micro(model=model, input_tokens=total_tokens)
    except Exception:
        cost_micro = 0
    row = ClaudeCallLog(
        organization_id=ctx.organization_id if ctx is not None else None,
        model=model or _DEFAULT_VOYAGE_MODEL,
        input_tokens=total_tokens,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd_micro=int(cost_micro),
        feature_hint="graph_sync",
        status="metering_error" if usage_event_error is not None else "ok",
        usage_event_id=usage_event_id,
        retry_attempt=retry_attempt,
        parent_call_log_id=parent_call_log_id,
        trace_id=(
            str(ctx.trace_id or ctx.episode_name)
            if ctx is not None and (ctx.trace_id or ctx.episode_name)
            else None
        ),
    )
    try:
        with SessionLocal() as session:
            session.add(row)
            session.commit()
    except Exception as exc:
        logger.warning(
            "metered_voyage: claude_call_log write failed (model=%s tok=%d) — "
            "Voyage will bill but our capture undercounts error_type=%s",
            model,
            total_tokens,
            type(exc).__name__,
        )

    if usage_event_error is not None and strict:
        # Provider success is billable, so deliberately retain the committed
        # hold.  Raising keeps the durable episode outbox pending; releasing
        # here would turn real Voyage spend into free/unattributed work.
        raise GraphUsageMeteringError(
            "Voyage usage settlement failed for hard-admitted Graphiti call "
            f"(org={ctx.organization_id if ctx is not None else None}, model={model})"
        ) from usage_event_error


def _voyage_usage_event_payload(
    ctx: Any,
    *,
    model: str,
    total_tokens: int,
    request_sha256: str,
) -> dict[str, Any]:
    """JSON-safe canonical receipt shared by live and deferred settlement."""
    return {
        "organization_id": int(ctx.organization_id),
        "feature": Feature.GRAPH_SYNC.value,
        "model": str(model),
        "input_tokens": int(total_tokens),
        "output_tokens": 0,
        "user_id": int(ctx.user_id) if ctx.user_id is not None else None,
        "role_id": int(ctx.role_id) if ctx.role_id is not None else None,
        "entity_id": (
            str(ctx.candidate_id) if ctx.candidate_id is not None else None
        ),
        "candidate_id": (
            int(ctx.candidate_id) if ctx.candidate_id is not None else None
        ),
        "provider": "voyage",
        "request_sha256": request_sha256,
        "metadata": {
            "provider": "voyage",
            "candidate_id": (
                int(ctx.candidate_id) if ctx.candidate_id is not None else None
            ),
            "request_sha256": request_sha256,
            **({"episode_name": ctx.episode_name} if ctx.episode_name else {}),
            **({"trace_id": ctx.trace_id} if ctx.trace_id else {}),
        },
    }


def wrap_voyage_embedder(embedder: Any) -> Any:
    """Wrap a graphiti ``VoyageAIEmbedder`` so its underlying client meters
    every embed call. Returns the same embedder (for chaining)."""
    if isinstance(embedder.client, MeteredVoyageClient):
        return embedder
    embedder.client = MeteredVoyageClient(embedder.client)
    logger.info("metered_voyage: embedder client wrapped for metering")
    return embedder
