"""Async sister of ``metered_anthropic_client.MeteredAnthropicClient``.

Built 2026-05-26 to plug the Graphiti metering bypass. Graphiti's
``AnthropicClient`` uses ``AsyncAnthropic`` (async), and the sync
wrapper can't intercept those calls. Without this, every candidate
graph-sync (~5 add_episode calls per candidate) made Haiku 4.5 calls
that Anthropic billed but Tali never recorded — accounting for the
bulk of the residual reconciliation drift on Haiku.

**Scope intentionally small.** Graphiti only ever calls
``messages.create`` (forced tool-use, no streaming, no batches), so
this wrapper supports exactly that surface and passes everything else
through ``__getattr__`` to the underlying SDK.

**Org attribution via contextvar.** Graphiti's LLM client is built
ONCE at module load and reused across every candidate. We can't pass
``metering={"feature": ..., "organization_id": ...}`` per-call because
Graphiti's call sites are inside the library. Instead, callers set
``graph_metering_ctx`` to a ``GraphMeteringContext`` before invoking
``graphiti.add_episode``; the wrapper reads it back inside the call.
When unset (any path that forgot to populate it), the wrapper still
writes a ``claude_call_log`` row with ``organization_id=None`` so the
spend isn't lost — surfaced in the metering-gap dashboard.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

from anthropic import AsyncAnthropic

from ..models.claude_call_log import ClaudeCallLog
from ..models.usage_event import UsageEvent
from ..platform.database import SessionLocal
from .metered_anthropic_client import _extract_cache_creation_1h
from .pricing_service import Feature, raw_cost_usd_micro
from .usage_metering_service import record_event

logger = logging.getLogger("taali.metered_async_anthropic")


@dataclass(frozen=True)
class GraphMeteringContext:
    """Per-call attribution for a wrapped async Anthropic call.

    Set via ``graph_metering_ctx.set(...)`` immediately before invoking
    ``graphiti.add_episode``. Cleared by the caller after the call (or
    reset via the token returned from ``set``).
    """

    organization_id: int
    role_id: Optional[int] = None
    candidate_id: Optional[int] = None
    user_id: Optional[int] = None
    episode_name: Optional[str] = None


graph_metering_ctx: ContextVar[Optional[GraphMeteringContext]] = ContextVar(
    "graph_metering_ctx", default=None
)


class _AsyncMeteredMessages:
    """Wraps ``AsyncAnthropic.messages`` so ``create`` writes a
    ``claude_call_log`` row (+ a usage_event when org context is set)
    from ``response.usage``. Streaming and batches pass through
    unmetered — Graphiti doesn't use them and metering them properly
    needs the sync wrapper's surface.
    """

    def __init__(self, *, inner: Any):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        # Pass-through for resources we don't intercept (batches,
        # countTokens, etc). Anything billable on these paths is by
        # definition unmetered through this wrapper — same caveat the
        # sync wrapper has on ``messages.batches.*``.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def create(self, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "")
        try:
            response = await self._inner.create(**kwargs)
        except Exception:
            # Failed calls: log a row with status=sdk_error so the
            # failure-rate dashboard sees them. Tokens = 0 (no usage
            # came back). Always re-raise — never swallow.
            self._record_call_log_safe(
                model=model,
                usage=None,
                status="sdk_error",
                anthropic_request_id=None,
            )
            raise

        usage = getattr(response, "usage", None)
        request_id = _extract_request_id(response)

        # Write the usage_event FIRST (with org context if present), so
        # the call_log can FK-link to it — mirrors the sync wrapper's
        # invariant.
        usage_event_id = self._write_usage_event_safe(usage=usage, model=model)
        self._record_call_log_safe(
            model=model,
            usage=usage,
            status="ok" if usage is not None else "no_usage_on_response",
            anthropic_request_id=request_id,
            usage_event_id=usage_event_id,
        )
        return response

    # ----- internals ------------------------------------------------------

    def _record_call_log_safe(
        self,
        *,
        model: str,
        usage: Any,
        status: str,
        anthropic_request_id: Optional[str],
        usage_event_id: Optional[int] = None,
    ) -> None:
        """Write a ``ClaudeCallLog`` row. Never raises — metering failures
        must not break the underlying Anthropic call.
        """
        ctx = graph_metering_ctx.get()
        org_id = ctx.organization_id if ctx is not None else None

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

        row = ClaudeCallLog(
            organization_id=org_id,
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
        )
        try:
            with SessionLocal() as session:
                session.add(row)
                session.commit()
        except Exception:
            logger.exception(
                "metered_async_anthropic: claude_call_log write failed "
                "(model=%s status=%s). Call already happened — Anthropic "
                "will bill but our reconciliation will undercount.",
                model, status,
            )

    def _write_usage_event_safe(self, *, usage: Any, model: str) -> Optional[int]:
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

        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_creation_tokens = int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        cache_creation_1h_tokens = _extract_cache_creation_1h(usage)
        try:
            with SessionLocal() as fresh:
                event = record_event(
                    fresh,
                    organization_id=ctx.organization_id,
                    feature=Feature.GRAPH_SYNC,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                    cache_creation_1h_tokens=cache_creation_1h_tokens,
                    user_id=ctx.user_id,
                    role_id=ctx.role_id,
                    entity_id=str(ctx.candidate_id) if ctx.candidate_id else None,
                    metadata={"episode_name": ctx.episode_name} if ctx.episode_name else None,
                )
                fresh.commit()
                fresh.refresh(event)
                return int(event.id)
        except Exception:
            logger.exception(
                "metered_async_anthropic: usage_event write failed for "
                "org=%s model=%s", ctx.organization_id, model,
            )
            return None


class MeteredAsyncAnthropic:
    """Drop-in replacement for ``anthropic.AsyncAnthropic`` that meters
    ``messages.create`` calls. Built specifically for Graphiti's LLM
    client; pass via ``AnthropicClient(client=MeteredAsyncAnthropic(...))``.

    Why a separate class instead of extending ``MeteredAnthropicClient``:
    the sync wrapper's ``messages.create`` is synchronous and returns
    a synchronous response. Graphiti calls it inside an async coroutine
    that ``await``s — a sync method works only by accident on most SDK
    versions, and the SDK auto-retries pattern differs between sync and
    async clients. Keeping a parallel async class avoids subtle bugs.
    """

    def __init__(self, *, inner: AsyncAnthropic):
        self._inner = inner
        self._messages = _AsyncMeteredMessages(inner=inner.messages)

    @property
    def messages(self) -> _AsyncMeteredMessages:
        return self._messages

    @property
    def inner(self) -> AsyncAnthropic:
        return self._inner

    def __getattr__(self, name: str) -> Any:
        # Pass-through for anything else (e.g. .beta resources, .with_options).
        return getattr(self._inner, name)


def _extract_request_id(response: Any) -> Optional[str]:
    # Mirrors the sync wrapper's extractor — Anthropic exposes the
    # request id on the response object (``id`` field) and on the
    # underlying httpx response (``response.id`` in newer SDKs).
    for attr in ("id", "_request_id", "request_id"):
        val = getattr(response, attr, None)
        if isinstance(val, str) and val:
            return val
    return None
