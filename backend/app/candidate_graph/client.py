"""Graphiti client lifecycle.

A single shared ``Graphiti`` instance is built lazily on first call and
reused across requests. It owns the Neo4j driver internally; callers
should not open neo4j sessions directly.

Configuration:
- ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` / ``NEO4J_DATABASE``
  point at the Neo4j instance.
- ``ANTHROPIC_API_KEY`` is reused for Graphiti's LLM extraction pass.
- ``VOYAGE_API_KEY`` is required for embeddings. Without it ``is_configured()``
  returns False and all graph features degrade gracefully.

Tenancy: every Graphiti episode/entity is namespaced via ``group_id``,
which we always set to ``f"org-{organization_id}"``. Cross-org queries
never match because Graphiti's search filters on group_id by construction.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from ..services.provider_error_evidence import safe_provider_error_code

logger = logging.getLogger("taali.candidate_graph.client")


class GraphClientError(RuntimeError):
    """Secret-safe Graphiti client lifecycle failure."""


def _make_noop_cross_encoder():
    """Build a passthrough CrossEncoderClient that avoids the OpenAI default.

    Graphiti validates cross_encoder via isinstance(CrossEncoderClient), so we
    import and subclass it here (inside a function to defer the graphiti import
    until Graphiti is actually configured).
    """
    from graphiti_core.cross_encoder.client import CrossEncoderClient  # type: ignore[import-not-found]

    class _Noop(CrossEncoderClient):
        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            return [(p, 1.0) for p in passages]

    return _Noop()


_graphiti = None
_lock = threading.Lock()
_loop_lifecycle_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_stopping = False


def is_configured() -> bool:
    """True iff Neo4j + Voyage are both configured.

    Anthropic is always configured (Tali-wide requirement), so we only
    gate on the Graphiti-specific knobs.
    """
    from ..platform.config import settings

    return bool((settings.NEO4J_URI or "").strip()) and bool(
        (settings.VOYAGE_API_KEY or "").strip()
    )


def group_id_for_org(organization_id: int) -> str:
    """Stable Graphiti group_id derived from organization id."""
    return f"org-{int(organization_id)}"


def _start_background_loop() -> asyncio.AbstractEventLoop:
    """Start a daemon thread running an asyncio event loop.

    Graphiti's API is async-only; the rest of Tali is sync FastAPI/SQLAlchemy.
    Rather than threading async into every caller, we run a single daemon
    loop and dispatch coroutines onto it via ``run_coroutine_threadsafe``.
    """
    global _loop, _loop_thread, _loop_stopping
    with _loop_lifecycle_lock:
        if _loop_stopping:
            raise GraphClientError("graphiti_loop_stopping")
        if (
            _loop is not None
            and not _loop.is_closed()
            and _loop_thread is not None
            and _loop_thread.is_alive()
        ):
            return _loop
        loop = asyncio.new_event_loop()

        def _runner() -> None:
            global _loop, _loop_thread, _loop_stopping
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            finally:
                with _loop_lifecycle_lock:
                    if _loop is loop:
                        _loop_stopping = True
                try:
                    try:
                        pending = [
                            task
                            for task in asyncio.all_tasks(loop)
                            if not task.done()
                        ]
                        for task in pending:
                            task.cancel()
                        if pending:
                            loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.run_until_complete(loop.shutdown_default_executor())
                    finally:
                        asyncio.set_event_loop(None)
                        if not loop.is_closed():
                            loop.close()
                finally:
                    with _loop_lifecycle_lock:
                        if _loop is loop:
                            _loop = None
                            _loop_thread = None
                            _loop_stopping = False

        thread = threading.Thread(target=_runner, name="graphiti-loop", daemon=True)
        _loop = loop
        _loop_thread = thread
        try:
            thread.start()
        except BaseException:
            _loop = None
            _loop_thread = None
            _loop_stopping = False
            loop.close()
            raise
    logger.info("Graphiti background event loop started")
    return loop


def run_async(coro, *, timeout: float = 60.0):
    """Run an async coroutine on the shared loop and block until it returns.

    Used by the sync code paths (FastAPI handlers, SQLAlchemy listeners)
    that need to call Graphiti without becoming async themselves.

    **ContextVar propagation.** ``asyncio.run_coroutine_threadsafe`` does
    NOT copy the caller's contextvars to the target loop's thread —
    contextvars are thread-local plus task-local, so a value set in the
    caller's thread (e.g. ``graph_metering_ctx.set(...)`` in
    ``episodes.dispatch``) is invisible to code running inside ``coro``
    on the Graphiti loop thread.

    Symptom this fix addresses (caught 2026-05-27 via worker logs):
    ``metered_async_anthropic: graph_metering_ctx unset`` firing on
    every Graphiti call → claude_call_log rows landed but with
    ``organization_id=NULL`` → reconciliation's
    ``organization_id IN (...)`` filter excluded them → those calls were
    invisible to drift math even though the row existed in the table.

    Fix: snapshot the caller's context with ``contextvars.copy_context()``
    and re-apply each var inside a wrapper coroutine on the target loop.
    """
    import contextvars

    try:
        loop = _start_background_loop()
    except BaseException:
        close_coro = getattr(coro, "close", None)
        if callable(close_coro):
            close_coro()
        raise
    caller_ctx = contextvars.copy_context()
    wrapped_started = threading.Event()

    async def _wrapped():
        wrapped_started.set()
        # Re-apply every contextvar that had a value in the caller's
        # context. ``var.set(value)`` inside this coroutine scopes the
        # value to this task's local context (and any further coroutines
        # it awaits), so other coroutines on the same loop don't bleed
        # into each other.
        tokens = [(var, var.set(value)) for var, value in caller_ctx.items()]
        try:
            return await coro
        finally:
            for var, token in reversed(tokens):
                try:
                    var.reset(token)
                except Exception:
                    # Best-effort cleanup — never fail the call here.
                    pass

    wrapped = _wrapped()
    with _loop_lifecycle_lock:
        if _loop_stopping or _loop is not loop or loop.is_closed():
            wrapped.close()
            close_coro = getattr(coro, "close", None)
            if callable(close_coro):
                close_coro()
            raise GraphClientError("graphiti_loop_stopping")
        try:
            future = asyncio.run_coroutine_threadsafe(wrapped, loop)
        except BaseException:
            wrapped.close()
            close_coro = getattr(coro, "close", None)
            if callable(close_coro):
                close_coro()
            raise

    def _close_original_if_cancelled_before_start(done_future) -> None:
        if done_future.cancelled() and not wrapped_started.is_set():
            close_coro = getattr(coro, "close", None)
            if callable(close_coro):
                close_coro()

    future.add_done_callback(_close_original_if_cancelled_before_start)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        # Queue cancellation behind the submission callback. That guarantees
        # the SDK coroutine is either started and receives CancelledError, or
        # the shutdown callback above closes it before its first step.
        try:
            loop.call_soon_threadsafe(future.cancel)
        except RuntimeError:
            future.cancel()
        raise


def _stop_background_loop(*, join_timeout: float = 10.0) -> None:
    """Stop, join, and close the shared selector loop without dropping it."""

    global _loop_stopping
    with _loop_lifecycle_lock:
        loop = _loop
        thread = _loop_thread
        if loop is None:
            return
        should_request_stop = not _loop_stopping
        _loop_stopping = True
        if should_request_stop and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)

    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=join_timeout)
    if thread is not None and thread.is_alive():
        logger.error("Graphiti background event loop did not stop before timeout")


async def _init_graphiti_async():
    """Build the Graphiti instance entirely within the background event loop.

    All asyncio resources (Neo4j connection pool, etc.) are created on the
    background loop so there is never a cross-loop Future mismatch when
    subsequent calls dispatch coroutines via run_async.
    """
    from anthropic import AsyncAnthropic
    from graphiti_core import Graphiti  # type: ignore[import-not-found]
    from graphiti_core.driver.neo4j_driver import Neo4jDriver  # type: ignore[import-not-found]
    from graphiti_core.llm_client.anthropic_client import AnthropicClient  # type: ignore[import-not-found]
    from graphiti_core.llm_client.config import LLMConfig  # type: ignore[import-not-found]
    from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig  # type: ignore[import-not-found]

    from ..platform.config import settings
    from ..services.metered_async_anthropic_client import MeteredAsyncAnthropic

    # Graphiti makes Haiku 4.5 calls inside ``add_episode`` (entity +
    # edge extraction). Until 2026-05-26 those calls bypassed our
    # metering entirely — Graphiti's AnthropicClient builds its own
    # AsyncAnthropic, and our sync MeteredAnthropicClient can't
    # intercept async coroutines. Symptom in reconciliation: Anthropic
    # billed 19.18M Haiku input tokens on 2026-05-23; our claude_call_log
    # captured 3.03M — Graphiti accounted for the missing 16M.
    #
    # Wrap the AsyncAnthropic instance so every Graphiti LLM call writes
    # a claude_call_log row (and a usage_event when graph_metering_ctx
    # is set by the dispatch path).
    metered_async = MeteredAsyncAnthropic(
        inner=AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=120.0,
            max_retries=0,
        )
    )
    llm_client = AnthropicClient(
        config=LLMConfig(
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.GRAPHITI_LLM_MODEL,
            small_model=settings.GRAPHITI_LLM_SMALL_MODEL,
        ),
        client=metered_async,  # type: ignore[arg-type]
    )
    embedder = VoyageAIEmbedder(
        config=VoyageAIEmbedderConfig(
            api_key=settings.VOYAGE_API_KEY,
            embedding_model=settings.GRAPHITI_EMBEDDING_MODEL,
        )
    )
    # Meter Voyage embedding spend the same way as the Anthropic graph calls:
    # wrap the embedder's client so every embed() books a usage_event +
    # call_log attributed via graph_metering_ctx. Without this, Voyage spend
    # (the only non-Anthropic provider) is invisible to billing + the budget.
    from ..services.metered_voyage_embedder import wrap_voyage_embedder

    embedder = wrap_voyage_embedder(embedder)
    neo4j_driver = Neo4jDriver(
        uri=settings.NEO4J_URI,
        user=settings.NEO4J_USER,
        password=settings.NEO4J_PASSWORD,
        database=settings.NEO4J_DATABASE or "neo4j",
    )
    graphiti = Graphiti(
        llm_client=llm_client,
        embedder=embedder,
        graph_driver=neo4j_driver,
        cross_encoder=_make_noop_cross_encoder(),
    )
    try:
        await graphiti.build_indices_and_constraints()
        logger.info("Graphiti indices/constraints ready")
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_index_setup")
        logger.error("Graphiti index/constraint setup failed error_code=%s", code)
    logger.info(
        "Graphiti initialised (model=%s, embedder=%s, db=%s)",
        settings.GRAPHITI_LLM_MODEL,
        settings.GRAPHITI_EMBEDDING_MODEL,
        settings.NEO4J_DATABASE or "neo4j",
    )
    return graphiti


def get_graphiti():
    """Return the shared ``Graphiti`` instance, initialising it if needed.

    All async resources are created inside the shared background event loop
    to avoid cross-loop Future errors (neo4j async driver binds its
    connection pool to whichever loop is running when first awaited).
    """
    global _graphiti
    if _graphiti is not None:
        return _graphiti

    with _lock:
        if _graphiti is not None:
            return _graphiti
        if not is_configured():
            raise RuntimeError(
                "Graphiti is not configured (need NEO4J_URI and VOYAGE_API_KEY)"
            )
        initialization_error_code: str | None = None
        try:
            _graphiti = run_async(_init_graphiti_async(), timeout=120.0)
        except Exception as exc:
            initialization_error_code = safe_provider_error_code(
                exc,
                operation="graphiti_client_init",
            )
            logger.error(
                "Graphiti initialization failed error_code=%s",
                initialization_error_code,
            )
        if initialization_error_code is not None:
            # Raise outside the handler so the original provider exception is
            # not retained in the safe exception's ``__context__``.
            raise GraphClientError(initialization_error_code)
        return _graphiti


def close() -> None:
    """Shutdown helper — stop the loop and close Graphiti's driver."""
    global _graphiti
    with _lock:
        if _graphiti is not None:
            try:
                run_async(_graphiti.close(), timeout=10.0)
            except Exception as exc:
                code = safe_provider_error_code(exc, operation="graphiti_close")
                logger.error("Graphiti close failed error_code=%s", code)
            _graphiti = None
        _stop_background_loop()


def healthcheck() -> dict:
    """Return a small status payload for the protected Graphiti health route."""
    if not is_configured():
        return {"status": "unconfigured"}
    # Configuration alone is not readiness. Report initialization honestly so
    # operators do not begin a backfill against a driver that is not usable.
    if _graphiti is None:
        return {"status": "initializing"}
    try:
        run_async(_graphiti.driver.execute_query("RETURN 1 AS ok"))
        return {"status": "ok"}
    except Exception as exc:
        code = safe_provider_error_code(exc, operation="graphiti_healthcheck")
        logger.error("Graphiti healthcheck failed error_code=%s", code)
        return {"status": "error"}
