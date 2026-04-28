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
which we always set to ``f"org:{organization_id}"``. Cross-org queries
never match because Graphiti's search filters on group_id by construction.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger("taali.candidate_graph.client")


_graphiti = None
_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None


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
    return f"org:{int(organization_id)}"


def _start_background_loop() -> asyncio.AbstractEventLoop:
    """Start a daemon thread running an asyncio event loop.

    Graphiti's API is async-only; the rest of Tali is sync FastAPI/SQLAlchemy.
    Rather than threading async into every caller, we run a single daemon
    loop and dispatch coroutines onto it via ``run_coroutine_threadsafe``.
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

    Used by the sync code paths (FastAPI handlers, SQLAlchemy listeners)
    that need to call Graphiti without becoming async themselves.
    """
    loop = _start_background_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def get_graphiti():
    """Return the shared ``Graphiti`` instance.

    Raises ``RuntimeError`` if not configured. Build is lazy so the
    Graphiti package isn't imported (and Neo4j isn't probed) until the
    first real call.
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

        from graphiti_core import Graphiti  # type: ignore[import-not-found]
        from graphiti_core.driver.neo4j_driver import Neo4jDriver  # type: ignore[import-not-found]
        from graphiti_core.llm_client.anthropic_client import AnthropicClient  # type: ignore[import-not-found]
        from graphiti_core.llm_client.config import LLMConfig  # type: ignore[import-not-found]
        from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig  # type: ignore[import-not-found]

        from ..platform.config import settings

        llm_client = AnthropicClient(
            config=LLMConfig(
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.GRAPHITI_LLM_MODEL,
                small_model=settings.GRAPHITI_LLM_SMALL_MODEL,
            )
        )
        embedder = VoyageAIEmbedder(
            config=VoyageAIEmbedderConfig(
                api_key=settings.VOYAGE_API_KEY,
                embedding_model=settings.GRAPHITI_EMBEDDING_MODEL,
                embedding_dim=int(settings.GRAPHITI_EMBEDDING_DIMS),
            )
        )
        neo4j_driver = Neo4jDriver(
            uri=settings.NEO4J_URI,
            user=settings.NEO4J_USER,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE or "neo4j",
        )

        _graphiti = Graphiti(
            llm_client=llm_client,
            embedder=embedder,
            graph_driver=neo4j_driver,
        )
        # First-time index/constraint creation. Idempotent — safe on every boot.
        try:
            run_async(_graphiti.build_indices_and_constraints())
        except Exception:
            logger.exception("Graphiti index/constraint setup failed (non-fatal)")
        logger.info(
            "Graphiti initialised (model=%s, embedder=%s, db=%s)",
            settings.GRAPHITI_LLM_MODEL,
            settings.GRAPHITI_EMBEDDING_MODEL,
            settings.NEO4J_DATABASE or "neo4j",
        )
        return _graphiti


def close() -> None:
    """Shutdown helper — stop the loop and close Graphiti's driver."""
    global _graphiti, _loop, _loop_thread
    with _lock:
        if _graphiti is not None:
            try:
                run_async(_graphiti.close(), timeout=10.0)
            except Exception:
                logger.exception("Graphiti close raised (non-fatal)")
            _graphiti = None
        if _loop is not None and _loop.is_running():
            _loop.call_soon_threadsafe(_loop.stop)
            _loop = None
            _loop_thread = None


def healthcheck() -> dict:
    """Return a small status payload for ``/healthz/graphiti``."""
    if not is_configured():
        return {"status": "unconfigured"}
    try:
        graphiti = get_graphiti()
        # A trivial Cypher round-trip via Graphiti's driver.
        run_async(graphiti.driver.execute_query("RETURN 1 AS ok"))
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("Graphiti healthcheck failed: %s", exc)
        return {"status": "error", "message": str(exc)}
