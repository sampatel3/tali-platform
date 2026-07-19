"""Safe producer-side replay policy for ATS stage-move jobs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from ..models.background_job_run import BackgroundJobRun, JOB_KIND_WORKABLE_OP
from ..models.candidate_application import CandidateApplication
from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .ats_stage_move_receipt import (
    STAGE_MOVE_HISTORY_KEY,
    STAGE_MOVE_OPERATION_KEY,
)

_MOVE_STAGE_OP_TYPE = "move_stage"
_ACTIVE_RUN_STATUSES = frozenset({"dispatching", "queued", "running"})
_MAX_DISPATCH_ATTEMPTS = 100


class StageMoveDispatchBlocked(RuntimeError):
    """A producer cannot prove that publishing another job is safe."""


@dataclass(frozen=True)
class StageMoveDispatchDecision:
    action: Literal["enqueue", "reuse", "confirmed"]
    dispatch_key: str | None = None
    job_run_id: int | None = None


def stage_move_attempt_dispatch_key(operation_id: str, attempt: int) -> str:
    """Return a short globally stable key for exactly one delivery attempt."""

    digest = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()
    return f"stage-move-dispatch:{digest}:attempt:{int(attempt)}"


def _state(app: CandidateApplication) -> dict[str, Any]:
    return (
        app.integration_sync_state
        if isinstance(app.integration_sync_state, dict)
        else {}
    )


def _run_matches(row: BackgroundJobRun, *, organization_id: int) -> bool:
    counters = row.counters if isinstance(row.counters, dict) else {}
    return bool(
        int(row.organization_id) == int(organization_id)
        and str(row.kind) == JOB_KIND_WORKABLE_OP
        and str(counters.get("op_type") or "") == _MOVE_STAGE_OP_TYPE
    )


def _active(row: BackgroundJobRun) -> bool:
    return bool(
        row.finished_at is None and str(row.status or "") in _ACTIVE_RUN_STATUSES
    )


def _proves_pre_provider_failure(row: BackgroundJobRun) -> bool:
    counters = row.counters if isinstance(row.counters, dict) else {}
    progress = counters.get("progress")
    evidence = progress if isinstance(progress, dict) else counters
    return bool(
        str(row.status or "") == "failed"
        and row.finished_at is not None
        and evidence.get("provider_called") is False
        and str(evidence.get("failure_phase") or "") == "before_provider_claim"
    )


def _run_by_id(
    db: Session, *, run_id: object, organization_id: int
) -> BackgroundJobRun | None:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return None
    return (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.id == int(run_id))
        .one_or_none()
    )


def _run_by_attempt(
    db: Session,
    *,
    organization_id: int,
    operation_id: str,
    attempt: int,
) -> BackgroundJobRun | None:
    dispatch_key = stage_move_attempt_dispatch_key(operation_id, attempt)
    return (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.dispatch_key == dispatch_key)
        .one_or_none()
    )


def _matching_history(state: dict[str, Any], operation_id: str) -> dict | None:
    history = state.get(STAGE_MOVE_HISTORY_KEY)
    if not isinstance(history, list):
        return None
    return next(
        (
            dict(item)
            for item in reversed(history)
            if isinstance(item, dict)
            and str(item.get("operation_id") or "") == operation_id
        ),
        None,
    )


def _reuse_active_receipt_run(
    db: Session,
    *,
    receipt: dict[str, Any],
    organization_id: int,
) -> StageMoveDispatchDecision | None:
    row = _run_by_id(
        db,
        run_id=receipt.get("job_run_id"),
        organization_id=organization_id,
    )
    if row is None:
        return None
    if not _run_matches(row, organization_id=organization_id):
        raise StageMoveDispatchBlocked(
            "The ATS stage-move receipt points to a different background job"
        )
    if _active(row):
        return StageMoveDispatchDecision(action="reuse", job_run_id=int(row.id))
    return None


def plan_stage_move_dispatch(
    db: Session,
    *,
    app: CandidateApplication,
    organization_id: int,
    operation_id: str,
) -> StageMoveDispatchDecision:
    """Plan one publish while the caller holds the application row lock.

    A fresh job is allowed only when no prior job exists or durable evidence
    proves a terminal attempt stopped before the provider claim. Any ambiguous
    or unaccounted terminal state fails closed.
    """

    clean_operation_id = str(operation_id or "").strip()
    if not clean_operation_id:
        raise StageMoveDispatchBlocked("ATS stage-move identity is missing")
    try:
        require_no_other_unresolved_provider_operation(
            app,
            receipt_key=STAGE_MOVE_OPERATION_KEY,
            operation_id=clean_operation_id,
        )
    except UnresolvedProviderOperation as exc:
        raise StageMoveDispatchBlocked(str(exc)) from None

    state = _state(app)
    current_raw = state.get(STAGE_MOVE_OPERATION_KEY)
    current = dict(current_raw) if isinstance(current_raw, dict) else None
    attempt = 1
    if current is not None and str(current.get("operation_id") or "") == clean_operation_id:
        status = str(current.get("status") or "").strip().lower()
        if status == "confirmed":
            return StageMoveDispatchDecision(
                action="confirmed",
                job_run_id=(
                    int(current["job_run_id"])
                    if isinstance(current.get("job_run_id"), int)
                    and not isinstance(current.get("job_run_id"), bool)
                    else None
                ),
            )
        if (
            status == "manual_reconciliation_required"
            or current.get("provider_outcome_uncertain") is True
            or current.get("manual_reconciliation_required") is True
        ):
            raise StageMoveDispatchBlocked(
                "The prior ATS stage move needs exact remote-stage verification"
            )
        if status in {"provider_call_started", "provider_succeeded"}:
            reusable = _reuse_active_receipt_run(
                db,
                receipt=current,
                organization_id=organization_id,
            )
            if reusable is not None:
                return reusable
            raise StageMoveDispatchBlocked(
                "The prior ATS stage move crossed the provider boundary; verify it before retrying"
            )
        retry_authorized = bool(
            status == "retry_authorized"
            and current.get("reconciliation_retry_observation_id")
            and current.get("reconciliation_retry_authorized_by_actor_id") is not None
        )
        if not retry_authorized and (
            status != "failed" or current.get("provider_called") is not False
        ):
            raise StageMoveDispatchBlocked(
                "The prior ATS stage move has no proof that a retry is safe"
            )
        reusable = _reuse_active_receipt_run(
            db,
            receipt=current,
            organization_id=organization_id,
        )
        if reusable is not None:
            return reusable
        try:
            attempt = int(current.get("provider_attempts") or 0) + 1
        except (TypeError, ValueError):
            raise StageMoveDispatchBlocked(
                "The prior ATS stage-move attempt counter is invalid"
            ) from None
    else:
        historical = _matching_history(state, clean_operation_id)
        if historical is not None:
            if str(historical.get("status") or "").strip().lower() == "confirmed":
                return StageMoveDispatchDecision(action="confirmed")
            raise StageMoveDispatchBlocked(
                "An archived ATS stage move cannot be rearmed over newer evidence"
            )

    for candidate_attempt in range(attempt, _MAX_DISPATCH_ATTEMPTS + 1):
        row = _run_by_attempt(
            db,
            organization_id=organization_id,
            operation_id=clean_operation_id,
            attempt=candidate_attempt,
        )
        if row is None:
            return StageMoveDispatchDecision(
                action="enqueue",
                dispatch_key=stage_move_attempt_dispatch_key(
                    clean_operation_id, candidate_attempt
                ),
            )
        if not _run_matches(row, organization_id=organization_id):
            raise StageMoveDispatchBlocked(
                "The ATS stage-move dispatch identity belongs to another job"
            )
        if _active(row):
            return StageMoveDispatchDecision(
                action="reuse", job_run_id=int(row.id)
            )
        if not _proves_pre_provider_failure(row):
            raise StageMoveDispatchBlocked(
                "A prior ATS stage-move job ended without exact pre-provider failure evidence"
            )

    raise StageMoveDispatchBlocked(
        "The ATS stage move exceeded its safe dispatch-attempt limit"
    )


__all__ = [
    "StageMoveDispatchBlocked",
    "StageMoveDispatchDecision",
    "plan_stage_move_dispatch",
    "stage_move_attempt_dispatch_key",
]
