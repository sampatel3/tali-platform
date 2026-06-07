"""Celery tasks for Graphiti candidate-graph ingestion.

The candidate_graph SQLAlchemy listeners used to spawn an in-process daemon
thread per write to run Graphiti's ``add_episode`` (1-5s of LLM entity
extraction + Voyage embeddings + Neo4j writes). On the **web** service that
ingestion ran inside the uvicorn processes and competed with HTTP request
handling for CPU / the GIL: under a burst of writes (batch scoring, a Workable
sync, a recruiter advancing a cohort) the listeners spawned hundreds of
threads and starved the 2 web workers, so every request — even ``/health`` —
queued 1-4s behind them.

These tasks move the work onto the Celery worker pool (bounded concurrency,
off the request path). The listeners now just ``.delay()`` a row id — a cheap
Redis push from whatever process did the write — and the heavy sync runs here.
This also bounds the worker-side cost: batch scoring no longer fans out into an
unbounded swarm of daemon threads, just queued tasks the pool drains in order.

Coverage is identical to the old in-thread path: candidate profile/CV,
interview transcript, and pipeline-event episodes all still flow to Graphiti —
just executed on the worker. (Decision / realised-outcome episodes have their
own durable ``graph_episode_outbox`` path; this complements it.)

Registered in ``app.tasks.__init__`` so the worker doesn't ``NotRegistered``
them — the same trap documented for the other eager-imported tasks.
"""

from __future__ import annotations

import logging

from .celery_app import celery_app
from ..platform.database import SessionLocal

logger = logging.getLogger("taali.tasks.graph_ingest")

# A freshly-inserted row may not be committed yet when the worker picks the
# task up (the producer's transaction commits just after the after_insert
# flush). Retry a couple of times so the commit race can't silently drop the
# episode; a genuinely-absent row (producer rolled back, or the entity was
# deleted) simply no-ops after the bounded retries.
_NOT_FOUND_RETRY_COUNTDOWN = 10
_MAX_RETRIES = 3


@celery_app.task(
    name="app.tasks.graph_ingest_tasks.sync_candidate_to_graph",
    bind=True,
    max_retries=_MAX_RETRIES,
)
def sync_candidate_to_graph(self, candidate_id: int) -> dict:
    from ..models.candidate import Candidate
    from ..candidate_graph import sync as sync_module

    db = SessionLocal()
    try:
        candidate = (
            db.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
        )
        if candidate is None:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=_NOT_FOUND_RETRY_COUNTDOWN)
            return {"status": "skipped", "reason": "candidate_not_found", "id": candidate_id}
        # Cost gate: only sync candidates the recruiter or Tali has advanced
        # past initial screening. Rejected / not-yet-advanced candidates are
        # skipped to keep Graphiti extraction bounded.
        if not sync_module.should_sync_candidate_to_graph(candidate, db):
            return {"status": "skipped", "reason": "below_cost_gate", "id": candidate_id}
        # bill_* tags each claude_call_log / usage_event with the right org +
        # candidate so graph-sync spend flows into the role's monthly budget
        # instead of landing org=NULL.
        sync_module.sync_candidate(
            candidate,
            db=db,
            bill_organization_id=int(candidate.organization_id)
            if candidate.organization_id is not None else None,
            bill_candidate_id=int(candidate.id),
        )
        return {"status": "ok", "id": candidate_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.graph_ingest_tasks.sync_interview_to_graph",
    bind=True,
    max_retries=_MAX_RETRIES,
)
def sync_interview_to_graph(self, interview_id: int) -> dict:
    from ..models.application_interview import ApplicationInterview
    from ..candidate_graph import sync as sync_module

    db = SessionLocal()
    try:
        interview = (
            db.query(ApplicationInterview)
            .filter(ApplicationInterview.id == interview_id)
            .one_or_none()
        )
        if interview is None:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=_NOT_FOUND_RETRY_COUNTDOWN)
            return {"status": "skipped", "reason": "interview_not_found", "id": interview_id}
        # ApplicationInterview.organization_id is non-nullable — pass it
        # directly rather than relying on best-effort application-chain
        # resolution (which lands org=NULL when the relationship isn't loaded).
        sync_module.sync_interview(
            interview,
            db=db,
            bill_organization_id=int(interview.organization_id),
        )
        return {"status": "ok", "id": interview_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.graph_ingest_tasks.sync_event_to_graph",
    bind=True,
    max_retries=_MAX_RETRIES,
)
def sync_event_to_graph(self, event_id: int) -> dict:
    from ..models.candidate_application_event import CandidateApplicationEvent
    from ..candidate_graph import sync as sync_module

    db = SessionLocal()
    try:
        ev = (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.id == event_id)
            .one_or_none()
        )
        if ev is None:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=_NOT_FOUND_RETRY_COUNTDOWN)
            return {"status": "skipped", "reason": "event_not_found", "id": event_id}
        # Pass db + the event's (non-nullable) organization_id so the graph_sync
        # spend writes a per-org usage_event instead of landing org=NULL.
        sync_module.sync_event(
            ev,
            db=db,
            bill_organization_id=int(ev.organization_id),
        )
        return {"status": "ok", "id": event_id}
    finally:
        db.close()


__all__ = [
    "sync_candidate_to_graph",
    "sync_interview_to_graph",
    "sync_event_to_graph",
]
