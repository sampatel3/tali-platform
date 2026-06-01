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
from .metered_async_anthropic_client import graph_metering_ctx
from .pricing_service import Feature, voyage_cost_micro
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
        result = await self._inner.embed(*args, **kwargs)
        try:
            model = kwargs.get("model")
            if model is None and len(args) >= 2:
                model = args[1]
            total_tokens = int(getattr(result, "total_tokens", 0) or 0)
            if total_tokens > 0:
                _record_voyage_usage(
                    model=model or _DEFAULT_VOYAGE_MODEL, total_tokens=total_tokens
                )
        except Exception:
            # Metering must never break the embedding call.
            logger.exception("metered_voyage: usage capture failed (non-fatal)")
        return result


def _record_voyage_usage(*, model: str, total_tokens: int) -> None:
    """Write a usage_event (when org context is set) + a call_log row for one
    Voyage embed call. Never raises."""
    ctx = graph_metering_ctx.get()
    usage_event_id: Optional[int] = None

    if ctx is not None:
        try:
            with SessionLocal() as session:
                event = record_event(
                    session,
                    organization_id=ctx.organization_id,
                    feature=Feature.GRAPH_SYNC,
                    model=model,
                    input_tokens=total_tokens,
                    output_tokens=0,
                    user_id=ctx.user_id,
                    role_id=ctx.role_id,
                    entity_id=str(ctx.candidate_id) if ctx.candidate_id else None,
                    metadata={"provider": "voyage", "episode_name": ctx.episode_name}
                    if ctx.episode_name
                    else {"provider": "voyage"},
                )
                session.flush()  # populate event.id
                usage_event_id = int(event.id)
                session.commit()
        except Exception:
            logger.exception("metered_voyage: usage_event write failed (non-fatal)")
            usage_event_id = None
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
        status="ok",
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
