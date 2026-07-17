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
from .pricing_service import Feature, voyage_cost_micro
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

logger = logging.getLogger("taali.metered_voyage")

_DEFAULT_VOYAGE_MODEL = "voyage-3"


class MeteredVoyageClient:
    """Transparent wrapper around ``voyageai.AsyncClient`` — meters ``embed``.

    Everything except ``embed`` delegates to the wrapped client unchanged, so
    Graphiti's ``VoyageAIEmbedder`` keeps working exactly as before.
    """

    def __init__(self, inner: Any):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def embed(self, *args: Any, **kwargs: Any) -> Any:
        model = kwargs.get("model")
        if model is None and len(args) >= 2:
            model = args[1]
        model = str(model or _DEFAULT_VOYAGE_MODEL)
        ctx = graph_metering_ctx.get()
        reservation: CreditReservation | None = None
        if ctx is not None and ctx.require_hard_admission:
            if ctx.require_role_admission and ctx.role_id is None:
                raise GraphProviderAdmissionError(
                    "hard-admitted Graphiti call requires role attribution"
                )
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
                    sub_feature="graphiti_voyage",
                    metadata={
                        "provider": "voyage",
                        "model": model,
                        "episode_name": ctx.episode_name,
                    },
                    require_role_authority=bool(ctx.require_role_admission),
                )
            except AutomaticProviderAuthorityError as exc:
                raise GraphProviderAdmissionError(str(exc)) from exc
            if not mark_provider_attempt_started(
                reservation,
                provider="voyage",
            ):
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
        except Exception as exc:
            released = release_provider_usage_if_definitely_nonbillable(
                reservation,
                error=exc,
                reason="graphiti_voyage_provider_error",
            )
            _record_voyage_failure_evidence(
                model=model,
                error=exc,
                status=(
                    "sdk_ambiguous_error"
                    if reservation is not None and not released
                    else "sdk_error"
                ),
            )
            raise

        total_tokens = int(getattr(result, "total_tokens", 0) or 0)
        if reservation is not None:
            mark_provider_usage_succeeded(
                reservation,
                deferred_usage_event=(
                    _voyage_usage_event_payload(
                        ctx,
                        model=model,
                        total_tokens=total_tokens,
                    )
                    if ctx is not None and total_tokens > 0
                    else None
                ),
                provider="voyage",
            )
        if reservation is not None and total_tokens <= 0:
            # Voyage returned a billable response without the token count
            # needed for actual settlement.  Keep the hold and force the
            # durable outbox to retry rather than silently under-bill.
            raise GraphUsageMeteringError(
                "Voyage response did not include token usage for hard-admitted call"
            )
        if total_tokens > 0:
            _record_voyage_usage(
                model=model,
                total_tokens=total_tokens,
                credit_reservation=reservation,
                strict=reservation is not None,
            )
        return result


def _record_voyage_usage(
    *,
    model: str,
    total_tokens: int,
    credit_reservation: CreditReservation | None = None,
    strict: bool = False,
) -> None:
    """Write a usage_event (when org context is set) + a call_log row for one
    Voyage embed call. Raises only for strict hard-admission settlement."""
    ctx = graph_metering_ctx.get()
    usage_event_id: Optional[int] = None
    usage_event_error: Exception | None = None

    if ctx is not None:
        try:
            payload = _voyage_usage_event_payload(
                ctx,
                model=model,
                total_tokens=total_tokens,
            )
            with SessionLocal() as session:
                event = record_event(
                    session,
                    **payload,
                    credit_reservation=(
                        credit_reservation.as_metering_payload()
                        if credit_reservation is not None
                        else None
                    ),
                )
                session.flush()  # populate event.id
                usage_event_id = int(event.id)
                session.commit()
        except Exception as exc:
            logger.exception("metered_voyage: usage_event write failed")
            usage_event_id = None
            usage_event_error = exc
    else:
        # No org context (e.g. a graph SEARCH query embed, which runs outside
        # the graph_sync dispatch). call_log still records the spend so a
        # future Voyage reconciliation closes; only per-org billing is missed.
        logger.debug(
            "metered_voyage: graph_metering_ctx unset — voyage embed (model=%s, "
            "%d tok) booked to call_log only, no org attribution.",
            model,
            total_tokens,
        )

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
    )
    try:
        with SessionLocal() as session:
            session.add(row)
            session.commit()
    except Exception:
        logger.exception(
            "metered_voyage: claude_call_log write failed (model=%s tok=%d) — "
            "Voyage will bill but our capture undercounts.",
            model,
            total_tokens,
        )

    if usage_event_error is not None and strict:
        # Provider success is billable, so deliberately retain the committed
        # hold.  Raising keeps the durable episode outbox pending; releasing
        # here would turn real Voyage spend into free/unattributed work.
        raise GraphUsageMeteringError(
            "Voyage usage settlement failed for hard-admitted Graphiti call "
            f"(org={ctx.organization_id if ctx is not None else None}, model={model})"
        ) from usage_event_error


def _record_voyage_failure_evidence(
    *,
    model: str,
    error: BaseException,
    status: str,
) -> None:
    """Persist a zero-usage oracle row for a failed/ambiguous embed call."""

    ctx = graph_metering_ctx.get()
    try:
        with SessionLocal() as session:
            session.add(
                ClaudeCallLog(
                    organization_id=(
                        int(ctx.organization_id) if ctx is not None else None
                    ),
                    model=model or _DEFAULT_VOYAGE_MODEL,
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    cost_usd_micro=0,
                    feature_hint="graph_sync",
                    status=status,
                    error_reason=str(error)[:500],
                    trace_id=(
                        str(ctx.trace_id) if ctx is not None and ctx.trace_id else None
                    ),
                )
            )
            session.commit()
    except Exception:
        logger.exception("metered_voyage: provider failure evidence write failed")


def _voyage_usage_event_payload(
    ctx: Any,
    *,
    model: str,
    total_tokens: int,
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
        "metadata": {
            "provider": "voyage",
            **({"episode_name": ctx.episode_name} if ctx.episode_name else {}),
            **({"trace_id": ctx.trace_id} if ctx.trace_id else {}),
        },
    }


def wrap_voyage_embedder(embedder: Any) -> Any:
    """Wrap a graphiti ``VoyageAIEmbedder`` so its underlying client meters
    every embed call. Returns the same embedder (for chaining)."""
    try:
        embedder.client = MeteredVoyageClient(embedder.client)
        logger.info("metered_voyage: embedder client wrapped for metering")
    except Exception:
        logger.exception(
            "metered_voyage: failed to wrap embedder client — voyage spend "
            "will be uncaptured"
        )
    return embedder
