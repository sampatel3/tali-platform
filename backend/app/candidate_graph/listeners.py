"""SQLAlchemy event hooks routing writes into Graphiti.

Listeners self-disable when Graphiti isn't configured so dev/test runs pay no
cost. Each listener **enqueues a Celery task** (a cheap Redis push) rather than
doing the work inline — Graphiti's add_episode is async + slow (LLM extraction
takes 1-5s), and we never want to block the recruiter's HTTP response on it,
nor burn the web service's CPU on it.

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

from sqlalchemy import event

from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.listeners")


_registered = False
_lock = threading.Lock()


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

        @event.listens_for(Candidate, "after_insert")
        def _candidate_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _enqueue_candidate_sync(int(target.id))
            except Exception:
                logger.exception("after_insert listener crashed (suppressed)")

        @event.listens_for(Candidate, "after_update")
        def _candidate_after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _enqueue_candidate_sync(int(target.id))
            except Exception:
                logger.exception("after_update listener crashed (suppressed)")

        @event.listens_for(ApplicationInterview, "after_insert")
        def _interview_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _enqueue_interview_sync(int(target.id))
            except Exception:
                logger.exception("interview after_insert listener crashed (suppressed)")

        @event.listens_for(ApplicationInterview, "after_update")
        def _interview_after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _enqueue_interview_sync(int(target.id))
            except Exception:
                logger.exception("interview after_update listener crashed (suppressed)")

        @event.listens_for(CandidateApplicationEvent, "after_insert")
        def _event_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _enqueue_event_sync(int(target.id))
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
                _enqueue_candidate_sync(int(cand_id))
            except Exception:
                logger.exception(
                    "application after_update listener crashed (suppressed)"
                )

        _registered = True
        logger.info(
            "Graphiti listeners registered → Celery "
            "(Candidate + CandidateApplication + ApplicationInterview + Event)"
        )
