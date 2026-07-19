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
off the request path). The listeners now commit a ``GraphIngestDispatch`` row
with the source write, then publish only that secret-free operation id. Beat
recovers a lost broker kick, while worker and provider-start nonces prevent an
at-least-once broker from multiplying paid work. This also bounds worker-side
cost: batch scoring no longer fans out into an unbounded swarm of daemon
threads, just queued tasks the pool drains in order.

Coverage is identical to the old in-thread path: candidate profile/CV,
interview transcript, and pipeline-event episodes all still flow to Graphiti —
just executed on the worker. (Decision / realised-outcome episodes have their
own durable ``graph_episode_outbox`` path; this complements it.)

Registered in ``app.tasks.__init__`` so the worker doesn't ``NotRegistered``
them — the same trap documented for the other eager-imported tasks.

Each task resolves the owning application role before provider work, then
hard-admits every Graphiti Anthropic/Voyage call. Legacy direct task calls keep
their bounded Celery retry contract. Durable listener calls instead reopen only
when their exact attempt provably failed before either SDK; a post-marker loss
is retained as reconciliation evidence and never blindly replayed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .celery_app import celery_app
from .retry_safety import raise_secret_safe_task_retry as _retry_safely
from ..platform.database import SessionLocal

if TYPE_CHECKING:
    from ..candidate_graph.ingest_outbox import GraphIngestTaskPublisher

logger = logging.getLogger("taali.tasks.graph_ingest")

# A freshly-inserted row may not be committed yet when the worker picks the
# task up (the producer's transaction commits just after the after_insert
# flush). Retry a couple of times so the commit race can't silently drop the
# episode; a genuinely-absent row (producer rolled back, or the entity was
# deleted) simply no-ops after the bounded retries.
_NOT_FOUND_RETRY_COUNTDOWN = 10
_NOT_FOUND_MAX_RETRIES = 3
_PROVIDER_RETRY_CAP_SECONDS = 3_600
_PROVIDER_MAX_RETRIES = 8


def _provider_retry_countdown(retries: int) -> int:
    return min(_PROVIDER_RETRY_CAP_SECONDS, 60 * (2 ** max(int(retries), 0)))


def _listener_graph_role_is_active(
    db,
    *,
    organization_id: int,
    role_id: int,
) -> bool:
    """Return whether listener-originated provider work is still authorized.

    Listener tasks can sit in Redis while a recruiter pauses or turns off the
    role. Re-read the authoritative row at execution time so that stale queued
    work cannot start a new Graphiti/Voyage/Anthropic call after that hold.
    Explicit backfills call ``candidate_graph.sync`` directly and do not pass
    through this listener-only gate.
    """
    from ..models.role import Role
    from ..models.organization import Organization

    return (
        db.query(Role.id)
        .join(Organization, Organization.id == Role.organization_id)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
            Role.agentic_mode_enabled.is_(True),
            Role.agent_paused_at.is_(None),
            Organization.agent_workspace_paused_at.is_(None),
        )
        .scalar()
        is not None
    )


def _claim_durable_attempt(
    db,
    *,
    operation_id: str | None,
    dispatch_nonce: str | None,
    work_kind: str,
    entity_id: int,
):
    if operation_id is None:
        return None
    from ..candidate_graph import ingest_outbox

    return ingest_outbox.claim_worker_attempt(
        db,
        operation_id=str(operation_id),
        dispatch_nonce=dispatch_nonce,
        work_kind=str(work_kind),
        entity_id=int(entity_id),
    )


def _finish_durable_skip(db, claim, *, reason: str) -> str:
    if claim is None:
        return "skipped"
    from ..candidate_graph import ingest_outbox

    return ingest_outbox.finish_before_provider(db, claim, reason=reason)


def _durable_skip_result(db, claim, *, entity_id: int, reason: str) -> dict:
    disposition = _finish_durable_skip(db, claim, reason=reason)
    return {
        "status": (
            "support_review_required"
            if disposition == "support_review_required"
            else "skipped"
        ),
        "reason": (
            f"replay_source_unavailable:{reason}"
            if disposition == "support_review_required"
            else reason
        ),
        "id": int(entity_id),
    }


def _prepare_durable_provider(db, claim) -> str | None:
    """Return a pre-provider disposition, or None when preparation may start."""

    if claim is None:
        return None
    from ..candidate_graph import client as graph_client
    from ..candidate_graph import ingest_outbox

    if not graph_client.is_configured():
        ingest_outbox.finish_before_provider(
            db,
            claim,
            reason="graph_configuration_unavailable",
            retry=True,
        )
        return "configuration_unavailable"
    return None


def _durable_provider_attempt_callback(claim):
    if claim is None:
        return None

    def _mark() -> bool:
        from ..candidate_graph import ingest_outbox

        marker_db = SessionLocal()
        try:
            return ingest_outbox.mark_provider_attempt_started(marker_db, claim)
        finally:
            marker_db.close()

    return _mark


def _durable_operation_manifest_callback(
    claim,
    *,
    work_kind: str,
    entity_id: int,
):
    if claim is None:
        return None

    def _record(episodes) -> bool:
        from ..candidate_graph import ingest_outbox

        marker_db = SessionLocal()
        try:
            return ingest_outbox.record_operation_manifest(
                marker_db,
                claim,
                work_kind=str(work_kind),
                entity_id=int(entity_id),
                episodes=episodes,
            )
        finally:
            marker_db.close()

    return _record


def _finish_durable_provider(
    db, claim, *, error: Exception | None = None
) -> str | None:
    if claim is None:
        return None
    from ..candidate_graph import ingest_outbox

    return ingest_outbox.finish_provider_attempt(
        db,
        claim,
        succeeded=error is None,
        error=error,
    )


def _durable_failure_reason(disposition: str | None) -> str:
    if disposition == "reconciliation_required":
        return "provider_outcome_ambiguous"
    if disposition == "support_review_required":
        return "operation_manifest_source_drift"
    return "pre_provider_failure"


@celery_app.task(
    name="app.tasks.graph_ingest_tasks.sync_candidate_to_graph",
    bind=True,
    max_retries=_PROVIDER_MAX_RETRIES,
)
def sync_candidate_to_graph(
    self,
    candidate_id: int,
    operation_id: str | None = None,
    dispatch_nonce: str | None = None,
) -> dict:
    from ..models.candidate import Candidate
    from ..candidate_graph import sync as sync_module

    db = SessionLocal()
    try:
        claim = _claim_durable_attempt(
            db,
            operation_id=operation_id,
            dispatch_nonce=dispatch_nonce,
            work_kind="candidate",
            entity_id=int(candidate_id),
        )
        if operation_id is not None and claim is None:
            return {
                "status": "fenced",
                "reason": "duplicate_or_stale_delivery",
                "id": candidate_id,
                "operation_id": str(operation_id),
            }
        candidate = (
            db.query(Candidate).filter(Candidate.id == candidate_id).one_or_none()
        )
        if candidate is None:
            if claim is not None:
                return _durable_skip_result(
                    db,
                    claim,
                    entity_id=candidate_id,
                    reason="candidate_not_found",
                )
            if self.request.retries < _NOT_FOUND_MAX_RETRIES:
                raise self.retry(countdown=_NOT_FOUND_RETRY_COUNTDOWN)
            return {"status": "skipped", "reason": "candidate_not_found", "id": candidate_id}
        # Cost gate: only sync candidates the recruiter or Tali has advanced
        # past initial screening. Rejected / not-yet-advanced candidates are
        # skipped to keep Graphiti extraction bounded.
        role_id = sync_module.billing_role_id_for_candidate(candidate, db)
        if role_id is None:
            return _durable_skip_result(
                db,
                claim,
                entity_id=candidate_id,
                reason="below_cost_gate",
            )
        organization_id = getattr(candidate, "organization_id", None)
        if organization_id is None or not _listener_graph_role_is_active(
            db,
            organization_id=int(organization_id),
            role_id=int(role_id),
        ):
            return _durable_skip_result(
                db,
                claim,
                entity_id=candidate_id,
                reason="role_not_running",
            )
        # bill_* tags each claude_call_log / usage_event with the right org +
        # candidate so graph-sync spend flows into the role's monthly budget
        # instead of landing org=NULL.
        durable_disposition = _prepare_durable_provider(db, claim)
        if durable_disposition is not None:
            return {
                "status": "deferred" if durable_disposition == "configuration_unavailable" else "fenced",
                "reason": durable_disposition,
                "id": candidate_id,
            }
        try:
            sync_kwargs = {
                "db": db,
                "bill_organization_id": int(organization_id),
                "bill_role_id": int(role_id),
                "require_role_admission": True,
                "raise_on_error": True,
            }
            if claim is not None and claim.replay_exact_payload:
                sync_kwargs["force_resync"] = True
            provider_marker = _durable_provider_attempt_callback(claim)
            if provider_marker is not None:
                sync_kwargs["provider_attempt_callback"] = provider_marker
            manifest_marker = _durable_operation_manifest_callback(
                claim,
                work_kind="candidate",
                entity_id=int(candidate_id),
            )
            if manifest_marker is not None:
                sync_kwargs["operation_manifest_callback"] = manifest_marker
            sync_module.sync_candidate(candidate, **sync_kwargs)
        except Exception as exc:
            if claim is not None:
                disposition = _finish_durable_provider(db, claim, error=exc)
                return {
                    "status": disposition,
                    "reason": _durable_failure_reason(disposition),
                    "id": candidate_id,
                }
            # Admission happens before the provider, so a budget-denied retry
            # never produces free work. The bounded budget prevents poison
            # entities or configuration errors from retrying forever.
            _retry_safely(
                self, exc, operation="graph_sync_candidate",
                countdown=_provider_retry_countdown(self.request.retries),
                max_retries=_PROVIDER_MAX_RETRIES,
            )
        completion = _finish_durable_provider(db, claim)
        if claim is not None and completion != "complete":
            return {
                "status": completion,
                "reason": "operation_manifest_terminal_mismatch",
                "id": candidate_id,
            }
        return {"status": "ok", "id": candidate_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.graph_ingest_tasks.sync_interview_to_graph",
    bind=True,
    max_retries=_PROVIDER_MAX_RETRIES,
)
def sync_interview_to_graph(
    self,
    interview_id: int,
    operation_id: str | None = None,
    dispatch_nonce: str | None = None,
) -> dict:
    from ..models.application_interview import ApplicationInterview
    from ..candidate_graph import sync as sync_module

    db = SessionLocal()
    try:
        claim = _claim_durable_attempt(
            db,
            operation_id=operation_id,
            dispatch_nonce=dispatch_nonce,
            work_kind="interview",
            entity_id=int(interview_id),
        )
        if operation_id is not None and claim is None:
            return {
                "status": "fenced",
                "reason": "duplicate_or_stale_delivery",
                "id": interview_id,
                "operation_id": str(operation_id),
            }
        interview = (
            db.query(ApplicationInterview)
            .filter(ApplicationInterview.id == interview_id)
            .one_or_none()
        )
        if interview is None:
            if claim is not None:
                return _durable_skip_result(
                    db,
                    claim,
                    entity_id=interview_id,
                    reason="interview_not_found",
                )
            if self.request.retries < _NOT_FOUND_MAX_RETRIES:
                raise self.retry(countdown=_NOT_FOUND_RETRY_COUNTDOWN)
            return {"status": "skipped", "reason": "interview_not_found", "id": interview_id}
        application = getattr(interview, "application", None)
        role_id = getattr(application, "role_id", None)
        if role_id is None:
            return _durable_skip_result(
                db,
                claim,
                entity_id=interview_id,
                reason="role_attribution_unavailable",
            )
        if not _listener_graph_role_is_active(
            db,
            organization_id=int(interview.organization_id),
            role_id=int(role_id),
        ):
            return _durable_skip_result(
                db,
                claim,
                entity_id=interview_id,
                reason="role_not_running",
            )
        # ApplicationInterview.organization_id is non-nullable — pass it
        # directly rather than relying on best-effort application-chain
        # resolution (which lands org=NULL when the relationship isn't loaded).
        durable_disposition = _prepare_durable_provider(db, claim)
        if durable_disposition is not None:
            return {
                "status": "deferred" if durable_disposition == "configuration_unavailable" else "fenced",
                "reason": durable_disposition,
                "id": interview_id,
            }
        try:
            sync_kwargs = {
                "db": db,
                "bill_organization_id": int(interview.organization_id),
                "bill_role_id": int(role_id),
                "require_role_admission": True,
                "raise_on_error": True,
            }
            provider_marker = _durable_provider_attempt_callback(claim)
            if provider_marker is not None:
                sync_kwargs["provider_attempt_callback"] = provider_marker
            manifest_marker = _durable_operation_manifest_callback(
                claim,
                work_kind="interview",
                entity_id=int(interview_id),
            )
            if manifest_marker is not None:
                sync_kwargs["operation_manifest_callback"] = manifest_marker
            sync_module.sync_interview(interview, **sync_kwargs)
        except Exception as exc:
            if claim is not None:
                disposition = _finish_durable_provider(db, claim, error=exc)
                return {
                    "status": disposition,
                    "reason": _durable_failure_reason(disposition),
                    "id": interview_id,
                }
            _retry_safely(
                self, exc, operation="graph_sync_interview",
                countdown=_provider_retry_countdown(self.request.retries),
                max_retries=_PROVIDER_MAX_RETRIES,
            )
        completion = _finish_durable_provider(db, claim)
        if claim is not None and completion != "complete":
            return {
                "status": completion,
                "reason": "operation_manifest_terminal_mismatch",
                "id": interview_id,
            }
        return {"status": "ok", "id": interview_id}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.graph_ingest_tasks.sync_event_to_graph",
    bind=True,
    max_retries=_PROVIDER_MAX_RETRIES,
)
def sync_event_to_graph(
    self,
    event_id: int,
    operation_id: str | None = None,
    dispatch_nonce: str | None = None,
) -> dict:
    from ..models.candidate_application_event import CandidateApplicationEvent
    from ..candidate_graph import sync as sync_module

    db = SessionLocal()
    try:
        claim = _claim_durable_attempt(
            db,
            operation_id=operation_id,
            dispatch_nonce=dispatch_nonce,
            work_kind="event",
            entity_id=int(event_id),
        )
        if operation_id is not None and claim is None:
            return {
                "status": "fenced",
                "reason": "duplicate_or_stale_delivery",
                "id": event_id,
                "operation_id": str(operation_id),
            }
        ev = (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.id == event_id)
            .one_or_none()
        )
        if ev is None:
            if claim is not None:
                return _durable_skip_result(
                    db,
                    claim,
                    entity_id=event_id,
                    reason="event_not_found",
                )
            if self.request.retries < _NOT_FOUND_MAX_RETRIES:
                raise self.retry(countdown=_NOT_FOUND_RETRY_COUNTDOWN)
            return {"status": "skipped", "reason": "event_not_found", "id": event_id}
        application = getattr(ev, "application", None)
        role_id = getattr(application, "role_id", None)
        if role_id is None:
            return _durable_skip_result(
                db,
                claim,
                entity_id=event_id,
                reason="role_attribution_unavailable",
            )
        if not _listener_graph_role_is_active(
            db,
            organization_id=int(ev.organization_id),
            role_id=int(role_id),
        ):
            return _durable_skip_result(
                db,
                claim,
                entity_id=event_id,
                reason="role_not_running",
            )
        # Pass db + the event's (non-nullable) organization_id so the graph_sync
        # spend writes a per-org usage_event instead of landing org=NULL.
        durable_disposition = _prepare_durable_provider(db, claim)
        if durable_disposition is not None:
            return {
                "status": "deferred" if durable_disposition == "configuration_unavailable" else "fenced",
                "reason": durable_disposition,
                "id": event_id,
            }
        try:
            sync_kwargs = {
                "db": db,
                "bill_organization_id": int(ev.organization_id),
                "bill_role_id": int(role_id),
                "require_role_admission": True,
                "raise_on_error": True,
            }
            provider_marker = _durable_provider_attempt_callback(claim)
            if provider_marker is not None:
                sync_kwargs["provider_attempt_callback"] = provider_marker
            manifest_marker = _durable_operation_manifest_callback(
                claim,
                work_kind="event",
                entity_id=int(event_id),
            )
            if manifest_marker is not None:
                sync_kwargs["operation_manifest_callback"] = manifest_marker
            sync_module.sync_event(ev, **sync_kwargs)
        except Exception as exc:
            if claim is not None:
                disposition = _finish_durable_provider(db, claim, error=exc)
                return {
                    "status": disposition,
                    "reason": _durable_failure_reason(disposition),
                    "id": event_id,
                }
            _retry_safely(
                self, exc, operation="graph_sync_event",
                countdown=_provider_retry_countdown(self.request.retries),
                max_retries=_PROVIDER_MAX_RETRIES,
            )
        completion = _finish_durable_provider(db, claim)
        if claim is not None and completion != "complete":
            return {
                "status": completion,
                "reason": "operation_manifest_terminal_mismatch",
                "id": event_id,
            }
        return {"status": "ok", "id": event_id}
    finally:
        db.close()


def _task_publishers_by_kind() -> Mapping[str, GraphIngestTaskPublisher]:
    """Build the outbox dependency map from the live task module attributes.

    Constructing this at dispatch time preserves the established monkeypatch
    seam on each Celery task while keeping the storage service independent of
    the task layer.
    """

    return {
        "candidate": sync_candidate_to_graph,
        "interview": sync_interview_to_graph,
        "event": sync_event_to_graph,
    }


@celery_app.task(name="app.tasks.graph_ingest_tasks.dispatch_graph_ingest_outbox")
def dispatch_graph_ingest_outbox(operation_id: str) -> dict:
    """Claim and publish one committed graph-ingest intent."""

    from ..candidate_graph import ingest_outbox

    db = SessionLocal()
    try:
        return ingest_outbox.dispatch_one(
            db,
            operation_id=str(operation_id),
            publishers_by_kind=_task_publishers_by_kind(),
        )
    finally:
        db.close()


@celery_app.task(name="app.tasks.graph_ingest_tasks.sweep_graph_ingest_outbox")
def sweep_graph_ingest_outbox(limit: int = 200) -> dict:
    """Recover broker loss and stale attempts that never crossed provider."""

    from ..candidate_graph import ingest_outbox

    db = SessionLocal()
    try:
        reconciled = ingest_outbox.reconcile_stale_provider_attempts(
            db, limit=int(limit)
        )
        operation_ids = ingest_outbox.recoverable_operation_ids(db, limit=int(limit))
    finally:
        db.close()

    queued = 0
    failed = 0
    for operation_id in operation_ids:
        try:
            dispatch_graph_ingest_outbox.delay(str(operation_id))
            queued += 1
        except Exception:
            # Each row remains in its prior recoverable state for the next Beat.
            failed += 1
            logger.exception(
                "failed to kick graph ingest recovery operation_id=%s",
                operation_id,
            )
    return {
        "status": "ok",
        "found": len(operation_ids),
        "queued": queued,
        "failed": failed,
        "reconciliation_required": reconciled,
    }


__all__ = [
    "sync_candidate_to_graph",
    "sync_interview_to_graph",
    "sync_event_to_graph",
    "dispatch_graph_ingest_outbox",
    "sweep_graph_ingest_outbox",
]
