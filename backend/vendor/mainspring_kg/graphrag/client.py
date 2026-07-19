"""Graphiti client lifecycle (ported from tali ``candidate_graph/client.py``).

A single shared ``Graphiti`` instance is built lazily on first call and
reused across requests. It owns the Neo4j driver internally; callers
should not open neo4j sessions directly.

Configuration (read from ``os.environ`` — mainspring has no brand
settings object; tali read the same knobs off its pydantic ``settings``):
- ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` / ``NEO4J_DATABASE``
  point at the Neo4j instance.
- ``ANTHROPIC_API_KEY`` is reused for Graphiti's LLM extraction pass.
- ``VOYAGE_API_KEY`` is required for embeddings. Without it ``is_configured()``
  returns False and all graph features degrade gracefully.
- ``GRAPHITI_LLM_MODEL`` / ``GRAPHITI_LLM_SMALL_MODEL`` /
  ``GRAPHITI_EMBEDDING_MODEL`` mirror tali's defaults.

Tenancy: every Graphiti episode/entity is namespaced via ``group_id``,
which we always set to ``f"org-{organization_id}"``. Cross-org queries
never match because Graphiti's search filters on group_id by construction.

NOTE on the LLM-metering wrapper: tali wraps Graphiti's ``AsyncAnthropic``
with ``MeteredAsyncAnthropic`` so write-time entity extraction is metered.
The substrate has no such wrapper, so this client uses a plain
``AsyncAnthropic``. This only affects the *write* (``add_episode``) path's
metering; it does NOT touch the *read* (``driver.execute_query``) path the
GraphRAG queries + ``synthesise_prior`` use, so priors are unaffected.
``graphiti-core`` / ``neo4j`` are imported lazily (only at first real
Graphiti use), so this module imports without the optional extra.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("mainspring.knowledge_graph.graphrag.client")


def safe_error_code(error: BaseException, *, operation: str) -> str:
    """Return stable failure evidence without serializing provider/DB detail."""

    return f"{operation}:{type(error).__name__}"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


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
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None


def is_configured() -> bool:
    """True iff Neo4j + Voyage are both configured.

    Anthropic is always configured (platform-wide requirement), so we only
    gate on the Graphiti-specific knobs.
    """
    return bool(_env("NEO4J_URI").strip()) and bool(_env("VOYAGE_API_KEY").strip())


def group_id_for_org(organization_id: int) -> str:
    """Stable Graphiti group_id derived from organization id."""
    return f"org-{int(organization_id)}"


def _start_background_loop() -> asyncio.AbstractEventLoop:
    """Start a daemon thread running an asyncio event loop.

    Graphiti's API is async-only; the rest of the platform is sync
    FastAPI/SQLAlchemy. Rather than threading async into every caller, we
    run a single daemon loop and dispatch coroutines onto it via
    ``run_coroutine_threadsafe``.
    """
    global _loop, _loop_thread
    if _loop is not None and _loop_thread is not None and _loop_thread.is_alive():
        return _loop
    loop = asyncio.new_event_loop()

    def _runner() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_runner, name="graphiti-loop", daemon=True)
    thread.start()
    _loop = loop
    _loop_thread = thread
    logger.info("Graphiti background event loop started")
    return loop


def run_async(coro, *, timeout: float = 60.0):
    """Run an async coroutine on the shared loop and block until it returns.

    Used by the sync code paths that need to call Graphiti without becoming
    async themselves. Snapshots the caller's contextvars and re-applies
    them inside a wrapper coroutine on the target loop, so metering /
    tracing context set by the caller is visible to Graphiti's own LLM
    calls (``asyncio.run_coroutine_threadsafe`` does not copy contextvars
    across threads on its own).
    """
    import contextvars

    loop = _start_background_loop()
    caller_ctx = contextvars.copy_context()

    async def _wrapped():
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

    future = asyncio.run_coroutine_threadsafe(_wrapped(), loop)
    return future.result(timeout=timeout)


def _build_async_anthropic(api_key: str):
    """Return the AsyncAnthropic instance Graphiti's LLM client should use.

    Tali wraps this with ``MeteredAsyncAnthropic`` so write-time entity
    extraction is metered. The substrate has no metering wrapper, so we
    return a plain client. The read path never uses this object, so the
    GraphRAG priors are identical regardless.
    """
    from anthropic import AsyncAnthropic

    # The substrate currently exposes only driver-backed reads, but this object
    # is a full Graphiti dependency and therefore retains paid-write capability.
    # Keep SDK retries explicit even on the dormant surface so a future write
    # cannot turn one application attempt into several invisible wire attempts.
    return AsyncAnthropic(api_key=api_key, max_retries=0)


async def _init_graphiti_async():
    """Build the Graphiti instance entirely within the background event loop.

    All asyncio resources (Neo4j connection pool, etc.) are created on the
    background loop so there is never a cross-loop Future mismatch when
    subsequent calls dispatch coroutines via run_async.
    """
    from graphiti_core import Graphiti  # type: ignore[import-not-found]
    from graphiti_core.driver.neo4j_driver import Neo4jDriver  # type: ignore[import-not-found]
    from graphiti_core.llm_client.anthropic_client import AnthropicClient  # type: ignore[import-not-found]
    from graphiti_core.llm_client.config import LLMConfig  # type: ignore[import-not-found]
    from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig  # type: ignore[import-not-found]

    anthropic_api_key = _env("ANTHROPIC_API_KEY")
    voyage_api_key = _env("VOYAGE_API_KEY")
    llm_model = _env("GRAPHITI_LLM_MODEL", "claude-haiku-4-5-20251001")
    small_model = _env("GRAPHITI_LLM_SMALL_MODEL", "claude-haiku-4-5-20251001")
    embedding_model = _env("GRAPHITI_EMBEDDING_MODEL", "voyage-3")
    neo4j_database = _env("NEO4J_DATABASE", "neo4j")

    async_anthropic = _build_async_anthropic(anthropic_api_key)
    llm_client = AnthropicClient(
        config=LLMConfig(
            api_key=anthropic_api_key,
            model=llm_model,
            small_model=small_model,
        ),
        client=async_anthropic,  # type: ignore[arg-type]
    )
    embedder = VoyageAIEmbedder(
        config=VoyageAIEmbedderConfig(
            api_key=voyage_api_key,
            embedding_model=embedding_model,
        )
    )
    neo4j_driver = Neo4jDriver(
        uri=_env("NEO4J_URI"),
        user=_env("NEO4J_USER", "neo4j"),
        password=_env("NEO4J_PASSWORD"),
        database=neo4j_database or "neo4j",
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
        logger.error(
            "Graphiti index/constraint setup failed error_code=%s",
            safe_error_code(exc, operation="graphiti_index_setup"),
        )
    logger.info(
        "Graphiti initialised (model=%s, embedder=%s, db=%s)",
        llm_model,
        embedding_model,
        neo4j_database or "neo4j",
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
        _graphiti = run_async(_init_graphiti_async(), timeout=120.0)
        return _graphiti


def close() -> None:
    """Shutdown helper — stop the loop and close Graphiti's driver."""
    global _graphiti, _loop, _loop_thread
    with _lock:
        if _graphiti is not None:
            try:
                run_async(_graphiti.close(), timeout=10.0)
            except Exception as exc:
                logger.error(
                    "Graphiti close failed error_code=%s",
                    safe_error_code(exc, operation="graphiti_close"),
                )
            _graphiti = None
        if _loop is not None and _loop.is_running():
            _loop.call_soon_threadsafe(_loop.stop)
            _loop = None
            _loop_thread = None


def healthcheck() -> dict:
    """Return a small status payload for a graph healthcheck route."""
    if not is_configured():
        return {"status": "unconfigured"}
    # If Graphiti hasn't finished initialising yet (first boot, index build
    # still running), return "ok" immediately so a deploy probe doesn't time
    # out and mark the deployment as failed. The full Neo4j round-trip probe
    # only runs once the instance is ready.
    if _graphiti is None:
        return {"status": "ok", "note": "initializing"}
    try:
        run_async(_graphiti.driver.execute_query("RETURN 1 AS ok"))
        return {"status": "ok"}
    except Exception as exc:
        code = safe_error_code(exc, operation="graphiti_healthcheck")
        logger.warning("Graphiti healthcheck failed error_code=%s", code)
        return {
            "status": "error",
            "message": "Graphiti healthcheck failed",
            "error_code": code,
        }
