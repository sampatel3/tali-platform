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
from ..platform.database import SessionLocal
from .metered_anthropic_client import _extract_cache_creation_1h
from .pricing_service import Feature, raw_cost_usd_micro
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
    trace_id: Optional[str] = None
    # Outbox dispatches set this flag so every actual provider call is
    # admitted against a durable organization + role hold before the SDK is
    # touched. Workspace searches use the same flag with no role, producing an
    # explicit organization-only hold rather than invented role spend.
    require_hard_admission: bool = False
    # Automatic role-owned work sets this separately. Workspace-level search
    # is still hard-admitted against the org, but deliberately has no invented
    # role attribution.
    require_role_admission: bool = False


class GraphProviderAdmissionError(AutomaticProviderAuthorityError):
    """A billed graph call is missing the role needed for hard admission."""


class GraphUsageMeteringError(RuntimeError):
    """The provider returned a billable result that could not be metered."""


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
        ctx = graph_metering_ctx.get()
        reservation: CreditReservation | None = None
        if ctx is not None and ctx.require_hard_admission:
            if ctx.require_role_admission and ctx.role_id is None:
                # Fail closed before touching Anthropic.  An org-only hold
                # would let autonomous graph spend escape the role ceiling.
                raise GraphProviderAdmissionError(
                    "hard-admitted Graphiti call requires role attribution"
                )
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
                    sub_feature="graphiti_anthropic",
                    metadata={
                        "provider": "anthropic",
                        "model": model,
                        "episode_name": ctx.episode_name,
                    },
                    require_role_authority=bool(ctx.require_role_admission),
                )
            except AutomaticProviderAuthorityError as exc:
                raise GraphProviderAdmissionError(str(exc)) from exc
            if not mark_provider_attempt_started(
                reservation,
                provider="anthropic",
            ):
                release_provider_usage(
                    reservation,
                    reason="graphiti_anthropic_attempt_marker_failed",
                )
                raise GraphProviderAdmissionError(
                    "could not durably mark Anthropic provider attempt"
                )

        try:
            response = await self._inner.create(**kwargs)
        except Exception as exc:
            # An allowlisted 4xx is a known rejection. Network,
            # timeout, 5xx, and unknown failures can occur after provider
            # acceptance, so retain their attempt marker for reconciliation.
            released = release_provider_usage_if_definitely_nonbillable(
                reservation,
                error=exc,
                reason="graphiti_anthropic_provider_error",
            )
            # Failed calls: log a row with status=sdk_error so the
            # failure-rate dashboard sees them. Tokens = 0 (no usage
            # came back). Always re-raise — never swallow.
            self._record_call_log_safe(
                model=model,
                usage=None,
                status=(
                    "sdk_ambiguous_error"
                    if reservation is not None and not released
                    else "sdk_error"
                ),
                anthropic_request_id=None,
            )
            raise

        usage = getattr(response, "usage", None)
        request_id = _extract_request_id(response)
        if reservation is not None:
            mark_provider_usage_succeeded(
                reservation,
                deferred_usage_event=(
                    _anthropic_usage_event_payload(ctx, usage=usage, model=model)
                    if ctx is not None and usage is not None
                    else None
                ),
                provider="anthropic",
                provider_request_id=request_id,
            )

        # Write the usage_event FIRST (with org context if present), so
        # the call_log can FK-link to it — mirrors the sync wrapper's
        # invariant.
        try:
            if reservation is not None and usage is None:
                # Anthropic accepted the request but supplied no accounting
                # payload.  Retain the hold conservatively and make the
                # durable outbox retry; never report the episode sent while
                # silently dropping its bill.
                raise GraphUsageMeteringError(
                    "Anthropic response did not include usage for hard-admitted call"
                )
            usage_event_id = self._write_usage_event_safe(
                usage=usage,
                model=model,
                credit_reservation=reservation,
                strict=reservation is not None,
            )
        except Exception:
            # The provider call happened, so releasing here would make real
            # spend free.  Keep the traceable hold and surface a hard error;
            # the outbox remains pending instead of marking the episode sent.
            self._record_call_log_safe(
                model=model,
                usage=usage,
                status="metering_error",
                anthropic_request_id=request_id,
            )
            raise
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

    def _write_usage_event_safe(
        self,
        *,
        usage: Any,
        model: str,
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

        payload = _anthropic_usage_event_payload(ctx, usage=usage, model=model)
        try:
            with SessionLocal() as fresh:
                event = record_event(
                    fresh,
                    **payload,
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
            logger.exception(
                "metered_async_anthropic: usage_event write failed for "
                "org=%s model=%s", ctx.organization_id, model,
            )
            if strict:
                raise GraphUsageMeteringError(
                    "Anthropic usage settlement failed for hard-admitted "
                    f"Graphiti call (org={ctx.organization_id}, model={model})"
                ) from exc
            return None


def _anthropic_usage_event_payload(
    ctx: GraphMeteringContext,
    *,
    usage: Any,
    model: str,
) -> dict[str, Any]:
    """JSON-safe canonical receipt shared by live and deferred settlement."""
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
        "entity_id": (
            str(ctx.candidate_id) if ctx.candidate_id is not None else None
        ),
        "metadata": {
            **({"episode_name": ctx.episode_name} if ctx.episode_name else {}),
            **({"trace_id": ctx.trace_id} if ctx.trace_id else {}),
            "provider": "anthropic",
        },
    }


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
