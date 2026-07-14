"""Durable state machine for role assessment-task provisioning.

This module owns request, claim, retry, and completion transitions.  Task
generation and persistence remain in :mod:`task_provisioning_service`; keeping
the state machine separate makes both responsibilities reviewable while the
legacy service continues to re-export the public API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role, role_tasks

PROVISIONING_PENDING = "pending"
PROVISIONING_AWAITING_ACTIVATION = "awaiting_activation"
PROVISIONING_RUNNING = "running"
PROVISIONING_RETRY_WAIT = "retry_wait"
PROVISIONING_FAILED = "failed"
PROVISIONING_BLOCKED = "blocked"
PROVISIONING_SUCCEEDED = "succeeded"
PROVISIONING_RECOVERABLE_STATUSES = frozenset(
    {
        PROVISIONING_PENDING,
        PROVISIONING_RUNNING,
        PROVISIONING_RETRY_WAIT,
        PROVISIONING_FAILED,
    }
)
PROVISIONING_STALE_AFTER = timedelta(minutes=15)


class TaskProvisioningError(RuntimeError):
    """Base error for a requested task that was not provisioned."""


class TaskProvisioningRetryableError(TaskProvisioningError):
    """A transient/configuration/generator failure that Celery should retry."""


class TaskProvisioningBlockedError(TaskProvisioningError):
    """The persisted role input is insufficient; a new publish can unblock it."""


class TaskProvisioningSupersededError(TaskProvisioningError):
    """A newer provisioning request replaced this worker's claim."""


@dataclass(frozen=True)
class TaskProvisioningClaim:
    status: str
    role: Role | None = None
    claim_token: str | None = None
    linked_task_id: int | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def task_provisioning_state(role: Role) -> dict[str, Any]:
    raw = getattr(role, "assessment_task_provisioning", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _linked_task_id(db: Session, role_id: int) -> int | None:
    row = db.execute(
        role_tasks.select()
        .with_only_columns(role_tasks.c.task_id)
        .where(role_tasks.c.role_id == int(role_id))
        .limit(1)
    ).first()
    return int(row[0]) if row is not None else None


def request_assessment_task_provisioning(
    role: Role,
    *,
    reason: str,
    supersede_generated_drafts: bool = False,
    defer_until_activation: bool = False,
    now: datetime | None = None,
) -> bool:
    """Persist generation intent on ``role`` in its caller-owned transaction.

    The broker kick happens only after commit at the route/sync boundary.  If
    that kick is lost, the Beat sweep finds this state and dispatches it.  A
    fresh request id also prevents a worker authoring against an older JD after
    the requisition is republished while generation is in flight.
    """
    current_time = now or _utcnow()
    try:
        linked = list(getattr(role, "tasks", None) or [])
    except Exception:
        linked = []
    superseded_task_ids: list[int] = []
    if supersede_generated_drafts and linked:
        for task in list(linked):
            extra = (
                dict(getattr(task, "extra_data", None))
                if isinstance(getattr(task, "extra_data", None), dict)
                else {}
            )
            # An inactive generated review draft has never become candidate-
            # facing, so a new JD safely supersedes it. Active tasks and any
            # manually authored/linked task are deliberate recruiter choices
            # and must not be silently replaced.
            if not (
                extra.get("generated")
                and extra.get("needs_review", True)
                and not bool(getattr(task, "is_active", False))
            ):
                continue
            task_id = getattr(task, "id", None)
            battle_state = (
                dict(extra.get("battle_test_provisioning"))
                if isinstance(extra.get("battle_test_provisioning"), dict)
                else {}
            )
            extra.update(
                {
                    "needs_review": False,
                    "superseded": True,
                    "superseded_at": _iso(current_time),
                    "superseded_reason": str(reason or "job_spec_changed")[:100],
                    "battle_test_provisioning": {
                        **battle_state,
                        "status": "superseded",
                        "claim_token": None,
                        "updated_at": _iso(current_time),
                    },
                }
            )
            task.extra_data = extra
            try:
                role.tasks.remove(task)
            except (AttributeError, ValueError):
                # Simple test doubles may expose a plain collection; the list
                # snapshot still ensures the state decision below is correct.
                pass
            linked.remove(task)
            if task_id is not None:
                superseded_task_ids.append(int(task_id))
    current_state = task_provisioning_state(role)
    activation_intent = (
        dict(current_state.get("activation_intent"))
        if isinstance(current_state.get("activation_intent"), dict)
        else None
    )
    if linked:
        linked_id = getattr(linked[0], "id", None)
        next_state = {
            "status": PROVISIONING_SUCCEEDED,
            "reason": str(reason or "role_updated")[:100],
            "attempts": int(current_state.get("attempts") or 0),
            "task_id": int(linked_id) if linked_id is not None else None,
            "updated_at": _iso(current_time),
            "completed_at": _iso(current_time),
        }
        if activation_intent:
            next_state["activation_intent"] = activation_intent
        role.assessment_task_provisioning = next_state
        return False

    request_id = uuid.uuid4().hex
    if activation_intent and supersede_generated_drafts:
        activation_intent.update(
            {
                "task_id": None,
                "provisioning_request_id": request_id,
                "updated_at": _iso(current_time),
            }
        )
        if str(activation_intent.get("status") or "") in {
            "pending",
            "retry_wait",
        } and defer_until_activation:
            activation_intent.update(
                {
                    "status": "blocked",
                    "last_error": (
                        "The requisition changed after Turn on was requested. "
                        "Review the updated job and press Turn on again."
                    ),
                    "next_attempt_at": None,
                    "blocked_at": _iso(current_time),
                }
            )
    next_state = {
        "status": (
            PROVISIONING_AWAITING_ACTIVATION
            if defer_until_activation
            else PROVISIONING_PENDING
        ),
        "reason": str(reason or "role_updated")[:100],
        "request_id": request_id,
        "attempts": 0,
        "requested_at": _iso(current_time),
        "updated_at": _iso(current_time),
        "last_error": None,
        "next_attempt_at": None,
        "superseded_task_ids": superseded_task_ids,
    }
    if activation_intent:
        next_state["activation_intent"] = activation_intent
    role.assessment_task_provisioning = next_state
    return not defer_until_activation


def authorize_assessment_task_provisioning(
    role: Role, *, reason: str, now: datetime | None = None
) -> bool:
    """Move a publish-time deferred request into the paid worker outbox."""
    current_time = now or _utcnow()
    state = task_provisioning_state(role)
    if not state:
        return request_assessment_task_provisioning(
            role, reason=reason, now=current_time
        )
    if str(state.get("status") or "") == PROVISIONING_SUCCEEDED:
        return False
    role.assessment_task_provisioning = {
        **state,
        "status": PROVISIONING_PENDING,
        "reason": str(reason or "agent_turn_on")[:100],
        "last_error": None,
        "next_attempt_at": None,
        "authorized_at": _iso(current_time),
        "updated_at": _iso(current_time),
    }
    return True


def provisioning_state_is_due(
    state: dict[str, Any], *, now: datetime | None = None
) -> bool:
    """Return whether Beat should recover this persisted request now."""
    current_time = now or _utcnow()
    status = str(state.get("status") or "")
    if status == PROVISIONING_PENDING:
        return True
    if status == PROVISIONING_RUNNING:
        updated_at = _parse_time(state.get("updated_at"))
        return updated_at is None or updated_at <= current_time - PROVISIONING_STALE_AFTER
    if status in {PROVISIONING_RETRY_WAIT, PROVISIONING_FAILED}:
        next_attempt_at = _parse_time(state.get("next_attempt_at"))
        return next_attempt_at is None or next_attempt_at <= current_time
    return False


def claim_assessment_task_provisioning(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    now: datetime | None = None,
) -> TaskProvisioningClaim:
    """Atomically claim one request; duplicate deliveries collapse here."""
    current_time = now or _utcnow()
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if role is None:
        return TaskProvisioningClaim(status="missing")

    linked_task_id = _linked_task_id(db, int(role.id))
    if linked_task_id is not None:
        state = task_provisioning_state(role)
        role.assessment_task_provisioning = {
            **state,
            "status": PROVISIONING_SUCCEEDED,
            "task_id": linked_task_id,
            "last_error": None,
            "next_attempt_at": None,
            "updated_at": _iso(current_time),
            "completed_at": _iso(current_time),
        }
        db.commit()
        return TaskProvisioningClaim(
            status="already_linked", role=role, linked_task_id=linked_task_id
        )

    state = task_provisioning_state(role)
    status = str(state.get("status") or PROVISIONING_PENDING)
    if status == PROVISIONING_RUNNING and not provisioning_state_is_due(
        state, now=current_time
    ):
        return TaskProvisioningClaim(status="already_running", role=role)
    if status in {PROVISIONING_SUCCEEDED, PROVISIONING_BLOCKED}:
        return TaskProvisioningClaim(status=status, role=role)

    claim_token = uuid.uuid4().hex
    role.assessment_task_provisioning = {
        **state,
        "status": PROVISIONING_RUNNING,
        "request_id": state.get("request_id") or uuid.uuid4().hex,
        "claim_token": claim_token,
        "attempts": int(state.get("attempts") or 0) + 1,
        "last_error": None,
        "next_attempt_at": None,
        "started_at": _iso(current_time),
        "updated_at": _iso(current_time),
    }
    db.commit()
    return TaskProvisioningClaim(
        status="claimed", role=role, claim_token=claim_token
    )


def finish_assessment_task_provisioning(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    claim_token: str,
    status: str,
    task_id: int | None = None,
    error: str | None = None,
    next_attempt_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Finish the current claim without overwriting a newer publish request."""
    current_time = now or _utcnow()
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
        )
        .with_for_update()
        .one_or_none()
    )
    if role is None:
        return False
    state = task_provisioning_state(role)
    if str(state.get("claim_token") or "") != str(claim_token or ""):
        db.rollback()
        return False
    terminal = status in {PROVISIONING_SUCCEEDED, PROVISIONING_BLOCKED}
    next_state = {
        **state,
        "status": status,
        "task_id": int(task_id) if task_id is not None else state.get("task_id"),
        "last_error": (str(error)[:2000] if error else None),
        "next_attempt_at": _iso(next_attempt_at) if next_attempt_at else None,
        "updated_at": _iso(current_time),
        "completed_at": _iso(current_time) if terminal else None,
    }
    if status == PROVISIONING_BLOCKED:
        activation_intent = (
            dict(next_state.get("activation_intent"))
            if isinstance(next_state.get("activation_intent"), dict)
            else {}
        )
        if str(activation_intent.get("status") or "") in {
            "pending",
            "retry_wait",
        }:
            activation_intent.update(
                {
                    "status": "blocked",
                    "last_error": (
                        "Assessment task provisioning is blocked: "
                        + str(error or "the requisition needs a usable job description")
                    )[:2000],
                    "next_attempt_at": None,
                    "blocked_at": _iso(current_time),
                    "updated_at": _iso(current_time),
                }
            )
            next_state["activation_intent"] = activation_intent
    role.assessment_task_provisioning = next_state
    db.commit()
    return True


def role_has_active_task(db: Session, role: Role) -> bool:
    """True if the role already links at least one active task."""
    try:
        tasks = list(getattr(role, "tasks", None) or [])
    except Exception:
        tasks = []
    return any(getattr(task, "is_active", False) for task in tasks)


def role_has_linked_task(role: Role) -> bool:
    """True if the role links any task, including an inactive review draft."""
    try:
        return bool(list(getattr(role, "tasks", None) or []))
    except Exception:
        return False


__all__ = [
    "PROVISIONING_AWAITING_ACTIVATION",
    "PROVISIONING_BLOCKED",
    "PROVISIONING_FAILED",
    "PROVISIONING_PENDING",
    "PROVISIONING_RECOVERABLE_STATUSES",
    "PROVISIONING_RETRY_WAIT",
    "PROVISIONING_RUNNING",
    "PROVISIONING_STALE_AFTER",
    "PROVISIONING_SUCCEEDED",
    "TaskProvisioningBlockedError",
    "TaskProvisioningClaim",
    "TaskProvisioningError",
    "TaskProvisioningRetryableError",
    "TaskProvisioningSupersededError",
    "_linked_task_id",
    "authorize_assessment_task_provisioning",
    "claim_assessment_task_provisioning",
    "finish_assessment_task_provisioning",
    "provisioning_state_is_due",
    "request_assessment_task_provisioning",
    "role_has_active_task",
    "role_has_linked_task",
    "task_provisioning_state",
]
