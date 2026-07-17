"""Durable dispatch and execution fencing for listener graph refreshes.

The source transaction owns creation of :class:`GraphIngestDispatch`.  This
module owns only short database claims and broker publication; no database
lock or open transaction crosses a broker or Graphiti/provider call.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.graph_ingest_dispatch import (
    GRAPH_INGEST_CLAIMED,
    GRAPH_INGEST_COMPLETE,
    GRAPH_INGEST_DISPATCHING,
    GRAPH_INGEST_PENDING,
    GRAPH_INGEST_PROVIDER_STARTED,
    GRAPH_INGEST_QUEUED,
    GRAPH_INGEST_RECONCILIATION,
    GRAPH_INGEST_SKIPPED,
    GraphIngestDispatch,
)
from .ingest_manifest import (
    build_operation_manifest,
    validate_operation_manifest,
)


_DISPATCH_STALE_AFTER = timedelta(minutes=5)
# A normal queue backlog must not continually rotate the nonce ahead of the
# accepted message.  Two hours is intentionally much longer than routine load.
_QUEUED_STALE_AFTER = timedelta(hours=2)
_PRE_PROVIDER_CLAIM_STALE_AFTER = timedelta(minutes=15)
# Candidate ingestion can legitimately dispatch up to 41 sequential episodes,
# each with a 120-second timeout. Four hours clears that worst case generously.
_PROVIDER_ATTEMPT_STALE_AFTER = timedelta(hours=4)
_BROKER_RETRY_DELAY = timedelta(seconds=15)
_CONFIG_RETRY_DELAY = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error_code(prefix: str, exc: Exception | None = None) -> str:
    suffix = type(exc).__name__ if exc is not None else "unknown"
    return f"{prefix}:{suffix}"[:128]


@dataclass(frozen=True)
class WorkerClaim:
    operation_id: str
    attempt_nonce: str
    replay_exact_payload: bool = False


class OperationManifestConflict(RuntimeError):
    """The live provider payload no longer matches immutable operation evidence."""


class GraphIngestTaskPublisher(Protocol):
    """Minimal Celery task surface injected by the task layer."""

    def delay(
        self,
        entity_id: int,
        *,
        operation_id: str,
        dispatch_nonce: str,
    ) -> object: ...


def _owner_authorized_exact_replay(row: GraphIngestDispatch) -> bool:
    from .ingest_reconciliation import owner_authorized_exact_replay

    return owner_authorized_exact_replay(row)


def _claim_dispatch(db: Session, operation_id: str) -> tuple[GraphIngestDispatch, str] | None:
    """Claim one due intent for broker publication via compare-and-update."""

    now = _now()
    nonce = str(uuid.uuid4())
    updated = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            or_(
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_PENDING,
                    or_(
                        GraphIngestDispatch.next_attempt_at.is_(None),
                        GraphIngestDispatch.next_attempt_at <= now,
                    ),
                ),
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_DISPATCHING,
                    or_(
                        GraphIngestDispatch.dispatched_at.is_(None),
                        GraphIngestDispatch.dispatched_at
                        < now - _DISPATCH_STALE_AFTER,
                    ),
                ),
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_QUEUED,
                    or_(
                        GraphIngestDispatch.dispatched_at.is_(None),
                        GraphIngestDispatch.dispatched_at < now - _QUEUED_STALE_AFTER,
                    ),
                ),
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
                    GraphIngestDispatch.provider_attempt_started_at.is_(None),
                    or_(
                        GraphIngestDispatch.claimed_at.is_(None),
                        GraphIngestDispatch.claimed_at
                        < now - _PRE_PROVIDER_CLAIM_STALE_AFTER,
                    ),
                ),
            ),
        )
        .update(
            {
                "status": GRAPH_INGEST_DISPATCHING,
                "dispatch_attempts": GraphIngestDispatch.dispatch_attempts + 1,
                "dispatch_nonce": nonce,
                "worker_attempt_nonce": None,
                "next_attempt_at": None,
                "dispatched_at": now,
                "claimed_at": None,
                "last_error_code": None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated != 1:
        return None
    row = db.get(GraphIngestDispatch, str(operation_id))
    return (row, nonce) if row is not None else None


def dispatch_one(
    db: Session,
    *,
    operation_id: str,
    publishers_by_kind: Mapping[str, GraphIngestTaskPublisher],
) -> dict:
    """Publish one exact intent, safely repeatable after broker ambiguity."""

    claimed = _claim_dispatch(db, str(operation_id))
    if claimed is None:
        existing = db.get(GraphIngestDispatch, str(operation_id))
        return {
            "status": "already_handled" if existing is not None else "missing",
            "operation_id": str(operation_id),
            "outbox_status": getattr(existing, "status", None),
        }
    row, dispatch_nonce = claimed

    task = publishers_by_kind.get(str(row.work_kind))
    if task is None:  # defensive: the migration/model constrain all producers
        db.query(GraphIngestDispatch).filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.status == GRAPH_INGEST_DISPATCHING,
            GraphIngestDispatch.dispatch_nonce == dispatch_nonce,
        ).update(
            {
                "status": GRAPH_INGEST_SKIPPED,
                "completed_at": _now(),
                "last_error_code": "unknown_work_kind",
            },
            synchronize_session=False,
        )
        db.commit()
        return {
            "status": "skipped",
            "operation_id": str(operation_id),
            "reason": "unknown_work_kind",
        }

    try:
        task.delay(
            int(row.entity_id),
            operation_id=str(operation_id),
            dispatch_nonce=dispatch_nonce,
        )
    except Exception as exc:
        # A broker exception can still be acceptance-ambiguous.  Reopening the
        # broker claim is safe because the actual worker requires this exact
        # nonce and then owns a second, durable pre-provider claim.
        db.rollback()
        db.query(GraphIngestDispatch).filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.status == GRAPH_INGEST_DISPATCHING,
            GraphIngestDispatch.dispatch_nonce == dispatch_nonce,
        ).update(
            {
                "status": GRAPH_INGEST_PENDING,
                "dispatch_nonce": None,
                "next_attempt_at": _now() + _BROKER_RETRY_DELAY,
                "last_error_code": _error_code("broker_publish", exc),
            },
            synchronize_session=False,
        )
        db.commit()
        return {
            "status": "retry",
            "operation_id": str(operation_id),
            "error_code": "broker_publish",
            "error_type": type(exc).__name__,
        }

    # In eager mode (and in a very fast worker) the execution task can already
    # be terminal.  This nonce-guarded update deliberately leaves that state.
    queued = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.status == GRAPH_INGEST_DISPATCHING,
            GraphIngestDispatch.dispatch_nonce == dispatch_nonce,
        )
        .update(
            {"status": GRAPH_INGEST_QUEUED},
            synchronize_session=False,
        )
    )
    db.commit()
    return {
        "status": "queued" if queued == 1 else "worker_started",
        "operation_id": str(operation_id),
    }


def claim_worker_attempt(
    db: Session,
    *,
    operation_id: str,
    dispatch_nonce: str | None,
    work_kind: str,
    entity_id: int,
) -> WorkerClaim | None:
    """Fence duplicate Celery deliveries before any provider can start."""

    if not dispatch_nonce:
        return None
    attempt_nonce = str(uuid.uuid4())
    now = _now()
    updated = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.work_kind == str(work_kind),
            GraphIngestDispatch.entity_id == int(entity_id),
            GraphIngestDispatch.status.in_(
                (GRAPH_INGEST_DISPATCHING, GRAPH_INGEST_QUEUED)
            ),
            GraphIngestDispatch.dispatch_nonce == str(dispatch_nonce),
        )
        .update(
            {
                "status": GRAPH_INGEST_CLAIMED,
                "worker_attempt_nonce": attempt_nonce,
                "claimed_at": now,
                "provider_attempt_started_at": None,
                "last_error_code": None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated != 1:
        return None
    row = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == str(operation_id),
            GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
            GraphIngestDispatch.worker_attempt_nonce == attempt_nonce,
        )
        .one_or_none()
    )
    if row is None:
        db.rollback()
        return None
    replay_exact_payload = _owner_authorized_exact_replay(row)
    db.rollback()
    return WorkerClaim(
        str(operation_id),
        attempt_nonce,
        replay_exact_payload=replay_exact_payload,
    )


def record_operation_manifest(
    db: Session,
    claim: WorkerClaim,
    *,
    work_kind: str,
    entity_id: int,
    episodes: Iterable[Any],
) -> bool:
    """CAS the immutable exact payload identity before the first provider call."""

    try:
        manifest, digest = build_operation_manifest(
            work_kind=str(work_kind),
            entity_id=int(entity_id),
            episodes=episodes,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise OperationManifestConflict(
            "live graph payload cannot be represented by a safe operation manifest"
        ) from exc
    updated = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.work_kind == str(work_kind),
            GraphIngestDispatch.entity_id == int(entity_id),
            GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
            GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
            GraphIngestDispatch.operation_manifest.is_(None),
            GraphIngestDispatch.operation_manifest_sha256.is_(None),
        )
        .update(
            {
                "operation_manifest": manifest,
                "operation_manifest_sha256": digest,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated == 1:
        return True
    existing = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.work_kind == str(work_kind),
            GraphIngestDispatch.entity_id == int(entity_id),
            GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
            GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
        )
        .one_or_none()
    )
    if existing is None:
        db.rollback()
        return False
    try:
        prior = validate_operation_manifest(
            existing.operation_manifest,
            existing.operation_manifest_sha256,
            work_kind=str(work_kind),
            entity_id=int(entity_id),
        )
    except ValueError as exc:
        db.rollback()
        raise OperationManifestConflict(
            "stored graph operation manifest requires support review"
        ) from exc
    db.rollback()
    if prior != manifest or str(existing.operation_manifest_sha256) != digest:
        raise OperationManifestConflict(
            "live graph payload differs from its immutable operation manifest"
        )
    return True


def mark_provider_attempt_started(db: Session, claim: WorkerClaim) -> bool:
    """Persist the no-auto-replay boundary immediately before Graphiti."""

    row = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.status.in_(
                (GRAPH_INGEST_CLAIMED, GRAPH_INGEST_PROVIDER_STARTED)
            ),
            GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
        )
        .populate_existing()
        .with_for_update(of=GraphIngestDispatch)
        .one_or_none()
    )
    if row is None:
        db.rollback()
        return False
    try:
        manifest = validate_operation_manifest(
            row.operation_manifest,
            row.operation_manifest_sha256,
            work_kind=str(row.work_kind),
            entity_id=int(row.entity_id),
        )
    except ValueError:
        row.status = GRAPH_INGEST_RECONCILIATION
        row.completed_at = _now()
        row.last_error_code = "operation_manifest_invalid"
        db.commit()
        return False
    if int(manifest["episode_count"]) < 1:
        row.status = GRAPH_INGEST_RECONCILIATION
        row.completed_at = _now()
        row.last_error_code = "operation_manifest_empty_provider_boundary"
        db.commit()
        return False

    # Graphiti can make several Anthropic/Voyage calls for one operation. The
    # same worker attempt may cross the marker repeatedly. Revalidate the
    # canonical manifest even on that idempotent path before returning
    # provider authority.
    if row.status == GRAPH_INGEST_PROVIDER_STARTED:
        valid_repeat = row.provider_attempt_started_at is not None
        db.rollback()
        return valid_repeat
    if row.provider_attempt_started_at is not None:
        db.rollback()
        return False
    row.status = GRAPH_INGEST_PROVIDER_STARTED
    row.provider_attempt_started_at = _now()
    db.commit()
    return True


def finish_before_provider(
    db: Session,
    claim: WorkerClaim,
    *,
    reason: str,
    retry: bool = False,
) -> str:
    """Finish or reopen an attempt that provably made no provider call."""

    exact_claim = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
            GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
            GraphIngestDispatch.provider_attempt_started_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=GraphIngestDispatch)
        .one_or_none()
    )
    if exact_claim is None:
        db.rollback()
        return "fenced"
    if (
        not retry
        and exact_claim.operation_manifest is not None
        and exact_claim.operation_manifest_sha256 is not None
    ):
        exact_claim.status = GRAPH_INGEST_RECONCILIATION
        exact_claim.completed_at = _now()
        exact_claim.last_error_code = f"replay_source_unavailable:{reason}"[:128]
        db.commit()
        return "support_review_required"

    values: dict = {
        "status": GRAPH_INGEST_PENDING if retry else GRAPH_INGEST_SKIPPED,
        "worker_attempt_nonce": None,
        "claimed_at": None,
        "last_error_code": str(reason)[:128],
    }
    if retry:
        values["next_attempt_at"] = _now() + _CONFIG_RETRY_DELAY
    else:
        values["completed_at"] = _now()
    for field, value in values.items():
        setattr(exact_claim, field, value)
    db.commit()
    return "retry" if retry else "skipped"


def finish_provider_attempt(
    db: Session,
    claim: WorkerClaim,
    *,
    succeeded: bool,
    error: Exception | None = None,
) -> str:
    """Record success, safe pre-provider retry, or ambiguous reconciliation."""

    now = _now()
    if succeeded:
        exact_attempt = (
            db.query(GraphIngestDispatch)
            .filter(
                GraphIngestDispatch.operation_id == claim.operation_id,
                GraphIngestDispatch.status.in_(
                    (GRAPH_INGEST_CLAIMED, GRAPH_INGEST_PROVIDER_STARTED)
                ),
                GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
            )
            .populate_existing()
            .with_for_update(of=GraphIngestDispatch)
            .one_or_none()
        )
        if exact_attempt is None:
            db.rollback()
            return "fenced"
        try:
            manifest = validate_operation_manifest(
                exact_attempt.operation_manifest,
                exact_attempt.operation_manifest_sha256,
                work_kind=str(exact_attempt.work_kind),
                entity_id=int(exact_attempt.entity_id),
            )
        except ValueError:
            exact_attempt.status = GRAPH_INGEST_RECONCILIATION
            exact_attempt.completed_at = now
            exact_attempt.last_error_code = "operation_manifest_invalid_completion"
            db.commit()
            return "support_review_required"
        episode_count = int(manifest["episode_count"])
        valid_terminal_shape = (
            exact_attempt.status == GRAPH_INGEST_CLAIMED and episode_count == 0
        ) or (
            exact_attempt.status == GRAPH_INGEST_PROVIDER_STARTED
            and episode_count > 0
            and exact_attempt.provider_attempt_started_at is not None
        )
        if not valid_terminal_shape:
            exact_attempt.status = GRAPH_INGEST_RECONCILIATION
            exact_attempt.completed_at = now
            exact_attempt.last_error_code = "operation_manifest_terminal_mismatch"
            db.commit()
            return "support_review_required"
        exact_attempt.status = GRAPH_INGEST_COMPLETE
        exact_attempt.completed_at = now
        exact_attempt.last_error_code = None
        db.commit()
        return "complete"

    # A changed or corrupt immutable manifest is not a transient construction
    # failure. Terminally fence it for support instead of reopening a poison
    # operation every five minutes. No provider marker was crossed.
    if isinstance(error, OperationManifestConflict):
        support_required = (
            db.query(GraphIngestDispatch)
            .filter(
                GraphIngestDispatch.operation_id == claim.operation_id,
                GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
                GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
                GraphIngestDispatch.provider_attempt_started_at.is_(None),
            )
            .update(
                {
                    "status": GRAPH_INGEST_RECONCILIATION,
                    "completed_at": now,
                    "last_error_code": "operation_manifest_source_drift",
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return "support_review_required" if support_required == 1 else "fenced"

    # An exception while the row is still claimed proves neither wrapped SDK
    # crossed its callback. Reopen only that safe pre-provider disposition.
    pre_provider = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
            GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
            GraphIngestDispatch.provider_attempt_started_at.is_(None),
        )
        .update(
            {
                "status": GRAPH_INGEST_PENDING,
                "worker_attempt_nonce": None,
                "claimed_at": None,
                "next_attempt_at": now + _CONFIG_RETRY_DELAY,
                "last_error_code": _error_code("pre_provider_failure", error),
            },
            synchronize_session=False,
        )
    )
    if pre_provider == 1:
        db.commit()
        return "retry"

    ambiguous = (
        db.query(GraphIngestDispatch)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.status == GRAPH_INGEST_PROVIDER_STARTED,
            GraphIngestDispatch.worker_attempt_nonce == claim.attempt_nonce,
        )
        .update(
            {
                "status": GRAPH_INGEST_RECONCILIATION,
                "completed_at": now,
                "last_error_code": _error_code(
                    "provider_outcome_ambiguous", error
                ),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if ambiguous == 1:
        return "reconciliation_required"
    support_state = (
        db.query(GraphIngestDispatch.last_error_code)
        .filter(
            GraphIngestDispatch.operation_id == claim.operation_id,
            GraphIngestDispatch.status == GRAPH_INGEST_RECONCILIATION,
            GraphIngestDispatch.last_error_code.in_(
                (
                    "operation_manifest_invalid",
                    "operation_manifest_empty_provider_boundary",
                    "operation_manifest_invalid_completion",
                    "operation_manifest_source_drift",
                    "operation_manifest_terminal_mismatch",
                )
            ),
        )
        .scalar()
    )
    db.rollback()
    return "support_review_required" if support_state is not None else "fenced"


def recoverable_operation_ids(db: Session, *, limit: int = 200) -> list[str]:
    """Return due broker/pre-provider work; provider-started rows are fenced."""

    now = _now()
    rows = (
        db.query(GraphIngestDispatch.operation_id)
        .filter(
            or_(
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_PENDING,
                    or_(
                        GraphIngestDispatch.next_attempt_at.is_(None),
                        GraphIngestDispatch.next_attempt_at <= now,
                    ),
                ),
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_DISPATCHING,
                    or_(
                        GraphIngestDispatch.dispatched_at.is_(None),
                        GraphIngestDispatch.dispatched_at
                        < now - _DISPATCH_STALE_AFTER,
                    ),
                ),
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_QUEUED,
                    or_(
                        GraphIngestDispatch.dispatched_at.is_(None),
                        GraphIngestDispatch.dispatched_at < now - _QUEUED_STALE_AFTER,
                    ),
                ),
                and_(
                    GraphIngestDispatch.status == GRAPH_INGEST_CLAIMED,
                    GraphIngestDispatch.provider_attempt_started_at.is_(None),
                    or_(
                        GraphIngestDispatch.claimed_at.is_(None),
                        GraphIngestDispatch.claimed_at
                        < now - _PRE_PROVIDER_CLAIM_STALE_AFTER,
                    ),
                ),
            )
        )
        .order_by(
            GraphIngestDispatch.created_at.asc(),
            GraphIngestDispatch.operation_id.asc(),
        )
        .limit(max(1, int(limit)))
        .all()
    )
    return [str(row[0]) for row in rows]


def reconcile_stale_provider_attempts(db: Session, *, limit: int = 200) -> int:
    """Surface abandoned post-marker work without ever replaying it."""

    now = _now()
    cutoff = now - _PROVIDER_ATTEMPT_STALE_AFTER
    stale_attempts = [
        (str(row[0]), row[1], row[2])
        for row in (
            db.query(
                GraphIngestDispatch.operation_id,
                GraphIngestDispatch.worker_attempt_nonce,
                GraphIngestDispatch.provider_attempt_started_at,
            )
            .filter(
                GraphIngestDispatch.status == GRAPH_INGEST_PROVIDER_STARTED,
                GraphIngestDispatch.provider_attempt_started_at.is_not(None),
                GraphIngestDispatch.provider_attempt_started_at < cutoff,
            )
            .order_by(
                GraphIngestDispatch.provider_attempt_started_at.asc(),
                GraphIngestDispatch.operation_id.asc(),
            )
            .limit(max(1, int(limit)))
            .all()
        )
    ]
    if not stale_attempts:
        return 0
    updated = 0
    for operation_id, attempt_nonce, provider_started_at in stale_attempts:
        updated += _reconcile_exact_stale_attempt(
            db,
            operation_id=operation_id,
            attempt_nonce=attempt_nonce,
            provider_started_at=provider_started_at,
            cutoff=cutoff,
            completed_at=now,
        )
    db.commit()
    return int(updated)


def _reconcile_exact_stale_attempt(
    db: Session,
    *,
    operation_id: str,
    attempt_nonce: str | None,
    provider_started_at: datetime,
    cutoff: datetime,
    completed_at: datetime,
) -> int:
    """CAS one selected snapshot so a fresh attempt cannot inherit stale fate."""

    exact_attempt = db.query(GraphIngestDispatch).filter(
        GraphIngestDispatch.operation_id == str(operation_id),
        GraphIngestDispatch.status == GRAPH_INGEST_PROVIDER_STARTED,
        GraphIngestDispatch.provider_attempt_started_at == provider_started_at,
        GraphIngestDispatch.provider_attempt_started_at < cutoff,
    )
    if attempt_nonce is None:
        exact_attempt = exact_attempt.filter(
            GraphIngestDispatch.worker_attempt_nonce.is_(None)
        )
    else:
        exact_attempt = exact_attempt.filter(
            GraphIngestDispatch.worker_attempt_nonce == str(attempt_nonce)
        )
    return int(
        exact_attempt.update(
            {
                "status": GRAPH_INGEST_RECONCILIATION,
                "completed_at": completed_at,
                "last_error_code": "provider_attempt_worker_lost",
            },
            synchronize_session=False,
        )
    )


__all__ = [
    "GraphIngestTaskPublisher",
    "OperationManifestConflict",
    "WorkerClaim",
    "claim_worker_attempt",
    "dispatch_one",
    "finish_before_provider",
    "finish_provider_attempt",
    "mark_provider_attempt_started",
    "record_operation_manifest",
    "reconcile_stale_provider_attempts",
    "recoverable_operation_ids",
]
