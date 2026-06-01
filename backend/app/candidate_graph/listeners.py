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
            # Cost gate: only sync candidates the recruiter or Tali has
            # advanced past initial screening. Rejected / not-yet-advanced
            # candidates are skipped to keep Graphiti extraction bounded.
            if not sync_module.should_sync_candidate_to_graph(candidate, db):
                return
            # Pass bill_organization_id so the metered async wrapper
            # around Graphiti's LLM client tags each claude_call_log
            # row with the right org (and writes a usage_event so the
            # graph-sync spend flows into the role's monthly budget).
            # Without this, Graphiti calls land in claude_call_log with
            # organization_id=NULL — reconciliation closes but
            # per-org spend display is wrong.
            sync_module.sync_candidate(
                candidate,
                db=db,
                bill_organization_id=int(candidate.organization_id)
                if candidate.organization_id is not None else None,
                bill_candidate_id=int(candidate.id),
            )
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
            # Attribute the indexing spend: ApplicationInterview.organization_id
            # is non-nullable, so pass it directly rather than relying on the
            # best-effort application-chain resolution (which lands org=NULL
            # when the relationship isn't loaded).
            sync_module.sync_interview(
                interview,
                db=db,
                bill_organization_id=int(interview.organization_id),
            )
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
            # Pass db + the event's (non-nullable) organization_id so the
            # graph_sync spend writes a per-org usage_event. The prior call
            # passed neither, so event-sync Anthropic calls always landed
            # org=NULL with no usage_event.
            sync_module.sync_event(
                ev,
                db=db,
                bill_organization_id=int(ev.organization_id),
            )
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
        from ..models.candidate_application import CandidateApplication
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

        @event.listens_for(CandidateApplication, "after_update")
        def _application_after_update(mapper, connection, target):  # noqa: ARG001
            """Trigger a candidate sync when an application transitions into
            a graph-worthy state. Without this hook, a candidate who was
            below the cost gate at insert time but later gets advanced
            (by Tali OR by Workable hand-back) would never reach Graphiti.

            The _sync_candidate_async itself re-checks the gate, so this
            listener stays cheap when the transition isn't graph-worthy.
            """
            try:
                cand_id = getattr(target, "candidate_id", None)
                if cand_id is None:
                    return
                _spawn(
                    _sync_candidate_async,
                    f"graphiti-candidate-{cand_id}-via-app-{target.id}",
                    int(cand_id),
                )
            except Exception:
                logger.exception(
                    "application after_update listener crashed (suppressed)"
                )

        _registered = True
        logger.info(
            "Graphiti listeners registered "
            "(Candidate + CandidateApplication + ApplicationInterview + Event)"
        )
