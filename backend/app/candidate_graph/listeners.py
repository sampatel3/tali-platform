"""Transaction-aware SQLAlchemy hooks routing committed writes into Graphiti.

Listeners self-disable when Graphiti isn't configured so dev/test runs pay no
cost. Mapper hooks only record row IDs in the owning SQLAlchemy transaction;
best-effort Celery enqueue is attempted after the *root* transaction commits.
A flush, SAVEPOINT release, or later rollback therefore cannot expose phantom
graph work to a worker.

Graphiti's ``add_episode`` is async + slow (LLM extraction takes 1-5s), so the
committed work still runs on the bounded Celery worker pool rather than inside
the web process. IDs are deduplicated within a transaction before the cheap
Redis pushes are made.

History: these listeners used to ``threading.Thread(...).start()`` a daemon
thread per write and run the sync in-process. On the web service that swarmed
the uvicorn workers — a burst of writes (batch scoring, a Workable sync) spun
up hundreds of threads doing LLM calls and starved request handling, so every
request queued seconds behind them. Moving execution to Celery fixed that;
deferring the push until root commit also aligns that work with Postgres.

Listeners cover three graph sources, in priority order:
1. Candidate (after_insert / after_update), plus CandidateApplication updates
   that can make an existing candidate graph-worthy.
2. ApplicationInterview (after_insert / after_update).
3. CandidateApplicationEvent (after_insert).

The graph thus stays roughly in sync with committed Postgres state, lagging by
the worker queue + one Graphiti extraction call.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.listeners")


_registered = False
_lock = threading.Lock()
_SESSION_PENDING_KEY = "candidate_graph_listener_pending"


@dataclass
class _PendingGraphIds:
    candidate_ids: set[int] = field(default_factory=set)
    interview_ids: set[int] = field(default_factory=set)
    event_ids: set[int] = field(default_factory=set)


def _enqueue_candidate_sync(candidate_id: int) -> None:
    """Best-effort Celery kick after the producer transaction commits."""
    try:
        from ..tasks.graph_ingest_tasks import sync_candidate_to_graph

        sync_candidate_to_graph.delay(int(candidate_id))
    except Exception:
        logger.exception("failed to enqueue candidate graph sync id=%s", candidate_id)


def _enqueue_interview_sync(interview_id: int) -> None:
    try:
        from ..tasks.graph_ingest_tasks import sync_interview_to_graph

        sync_interview_to_graph.delay(int(interview_id))
    except Exception:
        logger.exception("failed to enqueue interview graph sync id=%s", interview_id)


def _enqueue_event_sync(event_id: int) -> None:
    try:
        from ..tasks.graph_ingest_tasks import sync_event_to_graph

        sync_event_to_graph.delay(int(event_id))
    except Exception:
        logger.exception("failed to enqueue event graph sync id=%s", event_id)


def _pending_bucket(target: object) -> _PendingGraphIds | None:
    """Return the bucket owned by the target's current transaction scope.

    Explicit SAVEPOINT scopes get their own bucket. Committed nested buckets
    remain staged until the root commit; a nested rollback can therefore drop
    precisely its own lineage without erasing work recorded by the outer
    transaction for the same entity.
    """

    session = object_session(target)
    if session is None:
        return None
    owner_transaction = session.get_nested_transaction() or session.get_transaction()
    if owner_transaction is None:
        return None
    pending_by_transaction = session.info.setdefault(_SESSION_PENDING_KEY, {})
    return pending_by_transaction.setdefault(owner_transaction, _PendingGraphIds())


def _candidate_after_change(mapper, connection, target) -> None:  # noqa: ARG001
    try:
        bucket = _pending_bucket(target)
        if bucket is not None:
            bucket.candidate_ids.add(int(target.id))
    except Exception:
        logger.exception("candidate listener crashed (suppressed)")


def _interview_after_change(mapper, connection, target) -> None:  # noqa: ARG001
    try:
        bucket = _pending_bucket(target)
        if bucket is not None:
            bucket.interview_ids.add(int(target.id))
    except Exception:
        logger.exception("interview listener crashed (suppressed)")


def _event_after_insert(mapper, connection, target) -> None:  # noqa: ARG001
    try:
        bucket = _pending_bucket(target)
        if bucket is not None:
            bucket.event_ids.add(int(target.id))
    except Exception:
        logger.exception("event after_insert listener crashed (suppressed)")


def _application_after_update(mapper, connection, target) -> None:  # noqa: ARG001
    """Stage a candidate sync when an application may cross the cost gate."""

    try:
        candidate_id = getattr(target, "candidate_id", None)
        if candidate_id is None:
            return
        bucket = _pending_bucket(target)
        if bucket is not None:
            bucket.candidate_ids.add(int(candidate_id))
    except Exception:
        logger.exception("application after_update listener crashed (suppressed)")


def _merge_pending_ids(target: _PendingGraphIds, source: _PendingGraphIds) -> None:
    target.candidate_ids.update(source.candidate_ids)
    target.interview_ids.update(source.interview_ids)
    target.event_ids.update(source.event_ids)


def _dispatch_after_root_commit(committed_session: Session) -> None:
    """Hand nested work upward; dispatch only the committed root's bucket."""

    pending_by_transaction = committed_session.info.get(_SESSION_PENDING_KEY)
    if not pending_by_transaction:
        return

    # SQLAlchemy also emits after_commit when a SAVEPOINT is released. Its rows
    # are not externally visible yet, so transfer that exact bucket to its
    # parent instead of dispatching or leaving ambiguous ledger ownership.
    nested_transaction = committed_session.get_nested_transaction()
    if nested_transaction is not None:
        nested_pending = pending_by_transaction.pop(nested_transaction, None)
        if nested_pending is not None:
            parent_transaction = getattr(nested_transaction, "parent", None)
            if parent_transaction is None:  # pragma: no cover - defensive
                logger.warning("discarding graph IDs from parentless SAVEPOINT")
            else:
                parent_pending = pending_by_transaction.setdefault(
                    parent_transaction, _PendingGraphIds()
                )
                _merge_pending_ids(parent_pending, nested_pending)
        if not pending_by_transaction:
            committed_session.info.pop(_SESSION_PENDING_KEY, None)
        return

    root_transaction = committed_session.get_transaction()
    committed = (
        pending_by_transaction.pop(root_transaction, None)
        if root_transaction is not None
        else None
    )
    orphan_count = len(pending_by_transaction)
    committed_session.info.pop(_SESSION_PENDING_KEY, None)
    if orphan_count:
        # Never infer that an arbitrary bucket committed. Its ownership should
        # have been transferred in public SAVEPOINT order; dispatching it here
        # could resurrect work from a reset/rollback lifecycle edge.
        logger.warning(
            "discarding %s orphaned graph listener transaction bucket(s) at root commit",
            orphan_count,
        )
    if committed is None:
        return

    # Keep the existing source priority. Each helper suppresses broker errors,
    # so one failed kick cannot prevent later entity kinds from being queued or
    # retroactively affect the already-committed Postgres transaction.
    for candidate_id in sorted(committed.candidate_ids):
        _enqueue_candidate_sync(candidate_id)
    for interview_id in sorted(committed.interview_ids):
        _enqueue_interview_sync(interview_id)
    for event_id in sorted(committed.event_ids):
        _enqueue_event_sync(event_id)


def _is_same_or_descendant(transaction, ancestor) -> bool:
    current = transaction
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "parent", None)
    return False


def _discard_transaction_lineage(session: Session, transaction) -> None:
    pending_by_transaction = session.info.get(_SESSION_PENDING_KEY)
    if not pending_by_transaction:
        return
    for owner_transaction in tuple(pending_by_transaction):
        if _is_same_or_descendant(owner_transaction, transaction):
            pending_by_transaction.pop(owner_transaction, None)
    if not pending_by_transaction:
        session.info.pop(_SESSION_PENDING_KEY, None)


def _discard_after_soft_rollback(
    rolled_back_session: Session, previous_transaction
) -> None:
    """Discard only work owned by the rolled-back transaction lineage."""

    _discard_transaction_lineage(rolled_back_session, previous_transaction)


def _discard_after_transaction_end(ended_session: Session, ended_transaction) -> None:
    """Final cleanup for every ended transaction lineage.

    ``after_soft_rollback`` covers explicit rollback and lets us preserve work
    outside a rolled-back SAVEPOINT. SQLAlchemy does not emit it for every
    implicit rollback performed by ``Session.close()``/``Session.reset()``,
    though, and ``Session.info`` survives when that Session is reused. Every
    transaction end therefore removes IDs still owned by that scope or its
    descendants. Successful nested/root commits have already transferred or
    drained their buckets in ``after_commit``.
    """

    _discard_transaction_lineage(ended_session, ended_transaction)


def _listener_specs() -> tuple[tuple[object, str, Callable[..., None]], ...]:
    """Return stable named handlers for idempotent install and test cleanup."""

    from ..models.application_interview import ApplicationInterview
    from ..models.candidate import Candidate
    from ..models.candidate_application import CandidateApplication
    from ..models.candidate_application_event import CandidateApplicationEvent

    return (
        (Candidate, "after_insert", _candidate_after_change),
        (Candidate, "after_update", _candidate_after_change),
        (CandidateApplication, "after_update", _application_after_update),
        (ApplicationInterview, "after_insert", _interview_after_change),
        (ApplicationInterview, "after_update", _interview_after_change),
        (CandidateApplicationEvent, "after_insert", _event_after_insert),
        (Session, "after_commit", _dispatch_after_root_commit),
        (Session, "after_soft_rollback", _discard_after_soft_rollback),
        (Session, "after_transaction_end", _discard_after_transaction_end),
    )


def register_listeners() -> None:
    """Idempotently install transaction-aware graph ingestion listeners.

    No-op when Graphiti is not configured. Registration remains explicitly
    owned by the FastAPI startup path; importing this module in a Celery worker
    does not expand listener coverage or provider spend.
    """

    global _registered
    with _lock:
        if _registered:
            return
        if not graph_client.is_configured():
            logger.info("Graphiti not configured; skipping listener registration")
            return

        for target, event_name, handler in _listener_specs():
            if not event.contains(target, event_name, handler):
                event.listen(target, event_name, handler)

        _registered = True
        logger.info(
            "Graphiti listeners registered → Celery after root commit "
            "(Candidate + CandidateApplication + ApplicationInterview + Event)"
        )
