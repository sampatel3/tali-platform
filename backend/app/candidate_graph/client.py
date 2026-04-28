"""Neo4j driver lifecycle.

Single module-level driver, lazily initialised, reused across requests.
``is_configured()`` is the public predicate — call it BEFORE any other
graph work so the search runner can degrade gracefully when env vars
are missing.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("taali.candidate_graph.client")


_driver = None
_driver_lock = threading.Lock()


def is_configured() -> bool:
    """True iff a Neo4j URI is set in settings."""
    from ..platform.config import settings

    return bool((settings.NEO4J_URI or "").strip())


def get_driver():
    """Return the shared driver, building it on first call.

    Raises ``RuntimeError`` if Neo4j is not configured. Callers that
    want graceful degradation should gate on ``is_configured()`` first.
    """
    global _driver
    if _driver is not None:
        return _driver
    with _driver_lock:
        if _driver is not None:
            return _driver
        if not is_configured():
            raise RuntimeError("Neo4j is not configured (NEO4J_URI is empty)")
        from neo4j import GraphDatabase  # late import — driver not always installed locally

        from ..platform.config import settings

        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        logger.info(
            "Neo4j driver initialised (uri=%s, db=%s)",
            settings.NEO4J_URI,
            settings.NEO4J_DATABASE,
        )
        return _driver


def close_driver() -> None:
    """Test/shutdown helper: tear down the shared driver."""
    global _driver
    with _driver_lock:
        if _driver is not None:
            try:
                _driver.close()
            except Exception:
                logger.exception("Failed to close Neo4j driver")
            _driver = None


def session():
    """Open a Neo4j session against the configured database.

    Use as a context manager:

        with session() as s:
            s.run(...)
    """
    from ..platform.config import settings

    return get_driver().session(database=settings.NEO4J_DATABASE or "neo4j")


def healthcheck() -> dict:
    """Return a small status payload for ``/healthz/neo4j``."""
    if not is_configured():
        return {"status": "unconfigured"}
    try:
        with session() as s:
            record = s.run(
                "CALL dbms.components() YIELD versions, edition "
                "RETURN versions[0] AS version, edition LIMIT 1"
            ).single()
            if record is None:
                return {"status": "ok", "version": None, "edition": None}
            return {
                "status": "ok",
                "version": record.get("version"),
                "edition": record.get("edition"),
            }
    except Exception as exc:
        logger.warning("Neo4j healthcheck failed: %s", exc)
        return {"status": "error", "message": str(exc)}
