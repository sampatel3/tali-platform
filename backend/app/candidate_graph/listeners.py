"""SQLAlchemy event hooks routing writes into Graphiti.

Listeners self-disable when Graphiti isn't configured so dev/test runs pay no
cost. Each listener writes a durable dispatch intent in the source transaction,
then **enqueues a Celery dispatcher** after the outer commit. Graphiti's
add_episode is async + slow (LLM extraction takes 1-5s), and we never want to
block the recruiter's HTTP response on it, nor burn the web service's CPU on it.

History: these listeners used to ``threading.Thread(...).start()`` a daemon
thread per write and run the sync in-process. On the web service that swarmed
the uvicorn workers — a burst of writes (batch scoring, a Workable sync) spun
up hundreds of threads doing LLM calls and starved request handling, so every
request queued seconds behind them. Moving execution to the Celery worker pool
(see ``app.tasks.graph_ingest_tasks``) takes the work off the web request path
entirely and bounds its concurrency.

Listeners cover three sources, in priority order:
1. Candidate (after_insert / after_update) — profile, skills, experience,
   CV text. Cheap and fires on every recruiter edit.
2. ApplicationInterview (after_insert / after_update) — full transcript
   ingestion. The expensive one but runs at most once per interview.
3. CandidateApplicationEvent (after_insert) — pipeline note + stage
   transition. Cheap; we drop pure stage-only events upstream in
   build_event_episode.

The graph thus stays roughly in sync with Postgres, lagging by the worker's
queue + one Graphiti extraction call.
"""

from __future__ import annotations

import logging
import threading
import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.listeners")


_registered = False
_lock = threading.Lock()
_PENDING_KEY = "candidate_graph_after_commit"
_OUTBOX_IDS_KEY = "candidate_graph_ingest_outbox_ids"
_OUTBOX_BY_WORK_KEY = "candidate_graph_ingest_outbox_by_work"
_OUTBOX_TOUCHES_KEY = "candidate_graph_ingest_outbox_nested_touches"


def _enqueue_candidate_sync(candidate_id: int) -> None:
    """Queue a candidate graph-sync on the Celery worker pool (off the
    request path). Best-effort: a transient broker hiccup is logged, not
    fatal — the next write to the same candidate re-enqueues."""
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


def _dispatch_one(kind: str, entity_id: int) -> None:
    if kind == "candidate":
        _enqueue_candidate_sync(entity_id)
    elif kind == "interview":
        _enqueue_interview_sync(entity_id)
    elif kind == "event":
        _enqueue_event_sync(entity_id)
    else:  # pragma: no cover - internal invariant
        logger.error("unknown deferred graph sync kind=%s id=%s", kind, entity_id)


def _defer_until_commit(
    target,
    kind: str,
    entity_id: int,
    *,
    source_kind: str | None = None,
    source_entity_id: int | None = None,
) -> None:
    """Coalesce graph work for durable insertion in the source transaction.

    Mapper ``after_insert``/``after_update`` hooks run during flush. Publishing
    there races the worker against an uncommitted row and also publishes work
    for transactions that later roll back. Session-local deferral removes both
    failure modes without adding provider work to the request path.
    """
    session = object_session(target)
    if session is None:
        # Defensive fallback for unusual mapper usage outside a Session.
        _dispatch_one(kind, int(entity_id))
        return
    pending = session.info.setdefault(_PENDING_KEY, {})
    # Compatibility for a Session populated by the pre-outbox implementation
    # (or a test that deliberately exercises that representation).
    if isinstance(pending, set):
        pending.add((str(kind), int(entity_id)))
        return
    key = (str(kind), int(entity_id))
    intent = pending.setdefault(
        key,
        {
            "organization_id": getattr(target, "organization_id", None),
            "source_refs": set(),
            "transaction": (
                session.get_nested_transaction() or session.get_transaction()
            ),
        },
    )
    ref_kind = str(source_kind or kind)
    ref_id = int(source_entity_id if source_entity_id is not None else entity_id)
    intent["source_refs"].add((ref_kind, ref_id))


def _persist_pending_outbox(session: Session, flush_context=None) -> None:  # noqa: ARG001
    """Materialize coalesced intents before the source transaction commits."""

    pending = session.info.pop(_PENDING_KEY, {})
    if not pending or isinstance(pending, set):
        # Legacy sets are dispatched only by the compatibility post-commit path.
        if pending:
            session.info[_PENDING_KEY] = pending
        return

    from ..models.graph_ingest_dispatch import GraphIngestDispatch

    outbox_ids = session.info.setdefault(_OUTBOX_IDS_KEY, {})
    outbox_by_work = session.info.setdefault(_OUTBOX_BY_WORK_KEY, {})
    for (kind, entity_id), intent in sorted(pending.items()):
        source_refs = [
            {"kind": source_kind, "id": int(source_id)}
            for source_kind, source_id in sorted(intent["source_refs"])
        ]
        organization_id = intent.get("organization_id")
        work_key = (str(kind), int(entity_id))
        row = outbox_by_work.get(work_key)
        if row is None:
            operation_id = str(uuid.uuid4())
            row = GraphIngestDispatch(
                operation_id=operation_id,
                organization_id=(
                    int(organization_id) if organization_id is not None else None
                ),
                work_kind=str(kind),
                entity_id=int(entity_id),
                source_refs=source_refs,
            )
            session.add(row)
            outbox_by_work[work_key] = row
            outbox_ids[operation_id] = intent.get("transaction")
            continue

        # Several explicit flushes can occur inside one source transaction.
        # Preserve the old transaction-wide coalescing so they cannot fan out
        # duplicate paid work, while retaining every contributing source ref.
        merged_refs = {
            (str(ref.get("kind")), int(ref.get("id")))
            for ref in list(row.source_refs or []) + source_refs
        }
        row.source_refs = [
            {"kind": source_kind, "id": source_id}
            for source_kind, source_id in sorted(merged_refs)
        ]
        if row.organization_id is None and organization_id is not None:
            row.organization_id = int(organization_id)
        owner = outbox_ids.get(str(row.operation_id))
        intent_transaction = intent.get("transaction")
        if owner is not intent_transaction and intent_transaction is not None:
            touches = session.info.setdefault(_OUTBOX_TOUCHES_KEY, {})
            touches.setdefault(intent_transaction, set()).add(work_key)


def _dispatch_after_commit(session: Session) -> None:
    # Releasing a SAVEPOINT also emits after_commit. The durable row is not
    # externally visible until the root transaction commits.
    if session.in_nested_transaction():
        return
    outbox_ids = session.info.pop(_OUTBOX_IDS_KEY, {})
    session.info.pop(_OUTBOX_BY_WORK_KEY, None)
    session.info.pop(_OUTBOX_TOUCHES_KEY, None)
    for operation_id in sorted(outbox_ids):
        try:
            from ..tasks.graph_ingest_tasks import dispatch_graph_ingest_outbox

            dispatch_graph_ingest_outbox.delay(str(operation_id))
        except Exception:
            # The row is committed and remains pending; Beat will recover it.
            logger.exception(
                "failed to enqueue durable graph ingest operation_id=%s",
                operation_id,
            )

    # Backward-compatible handling for a Session populated using the former
    # in-memory tuple representation. Production mapper hooks are materialized
    # by _persist_pending_outbox and never use this path.
    pending = session.info.pop(_PENDING_KEY, set())
    if isinstance(pending, dict):
        pending = set(pending)
    for kind, entity_id in sorted(pending):
        _dispatch_one(kind, entity_id)


def _discard_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
    session.info.pop(_OUTBOX_IDS_KEY, None)
    session.info.pop(_OUTBOX_BY_WORK_KEY, None)
    session.info.pop(_OUTBOX_TOUCHES_KEY, None)


def _discard_rolled_back_work(session: Session, previous_transaction) -> None:
    """Discard only work owned by the rolled-back transaction/savepoint."""

    if getattr(previous_transaction, "parent", None) is None:
        _discard_after_rollback(session)
        return

    pending = session.info.get(_PENDING_KEY, {})
    if isinstance(pending, dict):
        for work_key, intent in list(pending.items()):
            if intent.get("transaction") is previous_transaction:
                pending.pop(work_key, None)

    outbox_ids = session.info.get(_OUTBOX_IDS_KEY, {})
    rolled_back_ids = {
        operation_id
        for operation_id, owner in list(outbox_ids.items())
        if owner is previous_transaction
    }
    for operation_id in rolled_back_ids:
        outbox_ids.pop(operation_id, None)

    outbox_by_work = session.info.get(_OUTBOX_BY_WORK_KEY, {})
    for work_key, row in list(outbox_by_work.items()):
        if str(getattr(row, "operation_id", "")) in rolled_back_ids:
            outbox_by_work.pop(work_key, None)

    touches = session.info.get(_OUTBOX_TOUCHES_KEY, {})
    for work_key in touches.pop(previous_transaction, set()):
        row = outbox_by_work.get(work_key)
        if row is not None:
            try:
                session.expire(row, ["source_refs", "organization_id"])
            except Exception:
                # A row created inside the rolled-back savepoint was removed
                # above; only still-persistent root rows need refreshing.
                pass


def register_listeners() -> None:
    """Idempotently install the SQLAlchemy event listeners.

    No-op when Graphiti is not configured — saves the listener overhead
    entirely on dev/test machines (and avoids enqueuing tasks that would
    no-op on a graph-less deployment).
    """
    global _registered
    with _lock:
        if _registered:
            return
        if not graph_client.is_configured():
            logger.info("Graphiti not configured; skipping listener registration")
            return

        from ..models.candidate import Candidate
        from ..models.candidate_application import CandidateApplication
        from ..models.application_interview import ApplicationInterview
        from ..models.candidate_application_event import CandidateApplicationEvent

        # Publish only after the transaction that produced the row succeeds.
        event.listen(Session, "after_flush_postexec", _persist_pending_outbox)
        event.listen(Session, "after_commit", _dispatch_after_commit)
        event.listen(Session, "after_soft_rollback", _discard_rolled_back_work)

        @event.listens_for(Candidate, "after_insert")
        def _candidate_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _defer_until_commit(
                    target,
                    "candidate",
                    int(target.id),
                    source_kind="candidate",
                    source_entity_id=int(target.id),
                )
            except Exception:
                logger.exception("after_insert listener crashed (suppressed)")

        @event.listens_for(Candidate, "after_update")
        def _candidate_after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _defer_until_commit(
                    target,
                    "candidate",
                    int(target.id),
                    source_kind="candidate",
                    source_entity_id=int(target.id),
                )
            except Exception:
                logger.exception("after_update listener crashed (suppressed)")

        @event.listens_for(ApplicationInterview, "after_insert")
        def _interview_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _defer_until_commit(
                    target,
                    "interview",
                    int(target.id),
                    source_kind="interview",
                    source_entity_id=int(target.id),
                )
            except Exception:
                logger.exception("interview after_insert listener crashed (suppressed)")

        @event.listens_for(ApplicationInterview, "after_update")
        def _interview_after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _defer_until_commit(
                    target,
                    "interview",
                    int(target.id),
                    source_kind="interview",
                    source_entity_id=int(target.id),
                )
            except Exception:
                logger.exception("interview after_update listener crashed (suppressed)")

        @event.listens_for(CandidateApplicationEvent, "after_insert")
        def _event_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _defer_until_commit(
                    target,
                    "event",
                    int(target.id),
                    source_kind="event",
                    source_entity_id=int(target.id),
                )
            except Exception:
                logger.exception("event after_insert listener crashed (suppressed)")

        @event.listens_for(CandidateApplication, "after_update")
        def _application_after_update(mapper, connection, target):  # noqa: ARG001
            """Trigger a candidate sync when an application transitions into
            a graph-worthy state. Without this hook, a candidate who was
            below the cost gate at insert time but later gets advanced
            (by Tali OR by Workable hand-back) would never reach Graphiti.

            The sync task itself re-checks the gate, so this listener stays
            cheap when the transition isn't graph-worthy.
            """
            try:
                cand_id = getattr(target, "candidate_id", None)
                if cand_id is None:
                    return
                _defer_until_commit(
                    target,
                    "candidate",
                    int(cand_id),
                    source_kind="application",
                    source_entity_id=int(target.id),
                )
            except Exception:
                logger.exception(
                    "application after_update listener crashed (suppressed)"
                )

        _registered = True
        logger.info(
            "Graphiti listeners registered → Celery "
            "(Candidate + CandidateApplication + ApplicationInterview + Event)"
        )
