"""SQLAlchemy event hooks for the candidate graph.

When a ``Candidate`` row is inserted or updated, fire-and-forget a
background sync of its graph projection. Errors are logged but never
raised — the recruiter's write succeeded regardless.

Listeners self-disable when ``NEO4J_URI`` is empty so local dev and
test runs don't pay any cost. Wired in ``main.py`` at startup via
``register_listeners()``.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import event

from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.listeners")


_registered = False
_lock = threading.Lock()


def _spawn_sync(candidate_id: int, organization_id: int) -> None:
    """Re-fetch the candidate in a fresh session and sync. Best-effort."""

    def _run() -> None:
        try:
            from ..platform.database import SessionLocal
            from ..models.candidate import Candidate
            from . import sync as sync_module

            db = SessionLocal()
            try:
                candidate = db.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
                if candidate is None:
                    return
                sync_module.sync_candidate(candidate, db=db)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Async graph sync failed for candidate=%s: %s", candidate_id, exc)

    threading.Thread(target=_run, name=f"graph-sync-{candidate_id}", daemon=True).start()


def register_listeners() -> None:
    """Idempotently install the SQLAlchemy event listeners.

    No-op when Neo4j isn't configured — saves the listener overhead
    entirely on dev/test machines.
    """
    global _registered
    with _lock:
        if _registered:
            return
        if not graph_client.is_configured():
            logger.info("Neo4j not configured; skipping graph sync listener registration")
            return

        from ..models.candidate import Candidate

        @event.listens_for(Candidate, "after_insert")
        def _after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn_sync(int(target.id), int(target.organization_id or 0))
            except Exception:
                logger.exception("after_insert listener crashed (suppressed)")

        @event.listens_for(Candidate, "after_update")
        def _after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn_sync(int(target.id), int(target.organization_id or 0))
            except Exception:
                logger.exception("after_update listener crashed (suppressed)")

        _registered = True
        logger.info("candidate_graph listeners registered (Candidate after_insert/update)")
