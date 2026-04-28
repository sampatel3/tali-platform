"""SQLAlchemy event hooks routing writes into Graphiti.

Listeners self-disable when Graphiti isn't configured so dev/test runs
pay no cost. Each listener spawns a daemon thread to call sync —
Graphiti's add_episode is async + slow (LLM extraction takes 1-5s),
and we never want to block the recruiter's HTTP response on it.

Listeners cover three sources, in priority order:
1. Candidate (after_insert / after_update) — profile, skills, experience,
   CV text. Cheap and fires on every recruiter edit.
2. ApplicationInterview (after_insert / after_update) — full transcript
   ingestion. The expensive one but runs at most once per interview.
3. CandidateApplicationEvent (after_insert) — pipeline note + stage
   transition. Cheap; we drop pure stage-only events upstream in
   build_event_episode.

The graph thus stays roughly in sync with Postgres, lagging by the
duration of one Graphiti extraction call.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import event

from . import client as graph_client

logger = logging.getLogger("taali.candidate_graph.listeners")


_registered = False
_lock = threading.Lock()


def _spawn(target, name: str, *args) -> None:
    threading.Thread(target=target, name=name, args=args, daemon=True).start()


def _sync_candidate_async(candidate_id: int) -> None:
    try:
        from ..platform.database import SessionLocal
        from ..models.candidate import Candidate
        from . import sync as sync_module

        db = SessionLocal()
        try:
            candidate = (
                db.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
            )
            if candidate is None:
                return
            sync_module.sync_candidate(candidate, db=db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Async candidate sync failed id=%s: %s", candidate_id, exc)


def _sync_interview_async(interview_id: int) -> None:
    try:
        from ..platform.database import SessionLocal
        from ..models.application_interview import ApplicationInterview
        from . import sync as sync_module

        db = SessionLocal()
        try:
            interview = (
                db.query(ApplicationInterview)
                .filter(ApplicationInterview.id == interview_id)
                .one_or_none()
            )
            if interview is None:
                return
            sync_module.sync_interview(interview, db=db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Async interview sync failed id=%s: %s", interview_id, exc)


def _sync_event_async(event_id: int) -> None:
    try:
        from ..platform.database import SessionLocal
        from ..models.candidate_application_event import CandidateApplicationEvent
        from . import sync as sync_module

        db = SessionLocal()
        try:
            ev = (
                db.query(CandidateApplicationEvent)
                .filter(CandidateApplicationEvent.id == event_id)
                .one_or_none()
            )
            if ev is None:
                return
            sync_module.sync_event(ev)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Async event sync failed id=%s: %s", event_id, exc)


def register_listeners() -> None:
    """Idempotently install the SQLAlchemy event listeners.

    No-op when Graphiti is not configured — saves the listener overhead
    entirely on dev/test machines.
    """
    global _registered
    with _lock:
        if _registered:
            return
        if not graph_client.is_configured():
            logger.info("Graphiti not configured; skipping listener registration")
            return

        from ..models.candidate import Candidate
        from ..models.application_interview import ApplicationInterview
        from ..models.candidate_application_event import CandidateApplicationEvent

        @event.listens_for(Candidate, "after_insert")
        def _candidate_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn(_sync_candidate_async, f"graphiti-candidate-{target.id}", int(target.id))
            except Exception:
                logger.exception("after_insert listener crashed (suppressed)")

        @event.listens_for(Candidate, "after_update")
        def _candidate_after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn(_sync_candidate_async, f"graphiti-candidate-{target.id}", int(target.id))
            except Exception:
                logger.exception("after_update listener crashed (suppressed)")

        @event.listens_for(ApplicationInterview, "after_insert")
        def _interview_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn(_sync_interview_async, f"graphiti-interview-{target.id}", int(target.id))
            except Exception:
                logger.exception("interview after_insert listener crashed (suppressed)")

        @event.listens_for(ApplicationInterview, "after_update")
        def _interview_after_update(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn(_sync_interview_async, f"graphiti-interview-{target.id}", int(target.id))
            except Exception:
                logger.exception("interview after_update listener crashed (suppressed)")

        @event.listens_for(CandidateApplicationEvent, "after_insert")
        def _event_after_insert(mapper, connection, target):  # noqa: ARG001
            try:
                _spawn(_sync_event_async, f"graphiti-event-{target.id}", int(target.id))
            except Exception:
                logger.exception("event after_insert listener crashed (suppressed)")

        _registered = True
        logger.info(
            "Graphiti listeners registered (Candidate + ApplicationInterview + CandidateApplicationEvent)"
        )
