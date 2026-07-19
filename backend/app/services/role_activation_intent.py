"""Durable one-click activation for requisition-backed role agents.

The browser records an authorization; it is never the workflow engine.  The
authorization lives inside ``Role.assessment_task_provisioning`` so the same
outbox that recovers task generation can carry activation through generation,
battle testing, repository verification, production readiness, and the first
cohort worker acknowledgement.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from .role_activation_policy import role_policy_snapshot as _role_policy_snapshot
from .role_activation_task_guard import (
    ACTIVATION_ACTIVE_STATUSES,
    ACTIVATION_BLOCKED,
    ACTIVATION_CANCELLED,
    ACTIVATION_PENDING,
    ACTIVATION_POLICY_MUTABLE_STATUSES,
    ACTIVATION_RETRY_WAIT,
    ACTIVATION_SUCCEEDED,
    activation_intent_is_due,
    activation_intent_state,
    activation_intent_task_ready,
    block_activation_intent_for_unavailable_selected_task,
    block_activation_intent_if_task_exhausted,
    iso_time as _iso,
    utcnow as _utcnow,
    write_activation_intent as _write_intent,
)
from .role_activation_task_selection import prepare_activation_task

def refresh_role_activation_intent_policy(
    role: Role, *, now: datetime | None = None
) -> bool:
    """Amend an unfinished Turn-on command with the newest saved policy.

    The intent is a durable authorization and recovery cursor, not a license
    to restore old settings. Recruiters may tighten automation while task
    generation, a readiness retry, or a HITL block is outstanding. Recording
    the new snapshot makes that ordering explicit for audit/recovery, while
    the activation worker treats the current locked Role row as authoritative.
    """
    intent = activation_intent_state(role)
    if str(intent.get("status") or "") not in ACTIVATION_POLICY_MUTABLE_STATUSES:
        return False
    snapshot = _role_policy_snapshot(role)
    if intent.get("policy_snapshot") == snapshot:
        return False
    current_time = now or _utcnow()
    revision = int(intent.get("policy_revision") or 0) + 1
    intent.update(snapshot)
    intent.update(
        {
            "policy_snapshot": snapshot,
            "policy_revision": revision,
            "policy_updated_at": _iso(current_time),
            "updated_at": _iso(current_time),
        }
    )
    _write_intent(role, intent)
    return True


def request_role_activation_intent(
    role: Role,
    *,
    user_id: int,
    monthly_budget_cents: int,
    auto_promote: bool = True,
    auto_send_assessment: bool | None = None,
    auto_resend_assessment: bool | None = None,
    auto_advance: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist (or idempotently refresh) the recruiter's Turn-on command."""
    current_time = now or _utcnow()
    budget = int(monthly_budget_cents)
    if budget <= 0:
        raise ValueError("monthly_budget_cents must be greater than zero")

    provisioning, exact_task_id, task_selection_error = prepare_activation_task(
        role,
        now=current_time,
    )

    existing = activation_intent_state(role)
    if str(existing.get("status") or "") in ACTIVATION_ACTIVE_STATUSES:
        request_id = str(existing.get("request_id") or uuid.uuid4().hex)
        requested_at = str(existing.get("requested_at") or _iso(current_time))
        attempts = int(existing.get("attempts") or 0)
    else:
        request_id = uuid.uuid4().hex
        requested_at = _iso(current_time)
        attempts = 0
    send_enabled = (
        bool(auto_promote)
        if auto_send_assessment is None
        else bool(auto_send_assessment)
    )
    resend_enabled = (
        bool(auto_promote)
        if auto_resend_assessment is None
        else bool(auto_resend_assessment)
    )
    advance_enabled = (
        bool(auto_promote) if auto_advance is None else bool(auto_advance)
    )
    # Save the authorized cap and action policy on the Role immediately. The
    # role remains powered off, but subsequent settings edits now have one
    # authoritative row to amend while the durable workflow is pending.
    role.monthly_usd_budget_cents = budget
    role.auto_promote = bool(auto_promote)
    role.auto_send_assessment = send_enabled
    role.auto_resend_assessment = resend_enabled
    role.auto_advance = advance_enabled
    policy_snapshot = _role_policy_snapshot(role)
    policy_revision = int(existing.get("policy_revision") or 0) + 1
    intent = {
        **existing,
        "command": "approve_when_ready",
        "status": (
            ACTIVATION_BLOCKED if task_selection_error else ACTIVATION_PENDING
        ),
        "request_id": request_id,
        "provisioning_request_id": provisioning.get("request_id"),
        "task_id": exact_task_id,
        **policy_snapshot,
        "policy_snapshot": policy_snapshot,
        "policy_revision": policy_revision,
        "policy_updated_at": _iso(current_time),
        "requested_by_user_id": int(user_id),
        "requested_at": requested_at,
        "last_requested_at": _iso(current_time),
        "updated_at": _iso(current_time),
        "attempts": attempts,
        "last_error": task_selection_error,
        "next_attempt_at": None,
        "cancelled_at": None,
        "completed_at": None,
    }
    if task_selection_error:
        intent["blocked_at"] = _iso(current_time)
    else:
        intent["blocked_at"] = None
    _write_intent(role, intent)
    provisioning = dict(role.assessment_task_provisioning or {})
    reconfiguration = provisioning.get("reconfiguration")
    if (
        not task_selection_error
        and isinstance(reconfiguration, dict)
        and str(reconfiguration.get("status") or "") == "blocked"
    ):
        provisioning["reconfiguration"] = {
            **reconfiguration,
            "status": "pending",
            "resolution": "preserved_task_confirmed_by_user",
            "confirmed_task_id": exact_task_id,
            "confirmed_by_user_id": int(user_id),
            "last_error": None,
            "updated_at": _iso(current_time),
        }
        role.assessment_task_provisioning = provisioning
    return intent


def cancel_role_activation_intent(
    role: Role,
    *,
    user_id: int | None,
    reason: str,
    now: datetime | None = None,
) -> bool:
    intent = activation_intent_state(role)
    if str(intent.get("status") or "") not in ACTIVATION_POLICY_MUTABLE_STATUSES:
        return False
    current_time = now or _utcnow()
    intent.update(
        {
            "status": ACTIVATION_CANCELLED,
            "cancelled_at": _iso(current_time),
            "cancelled_by_user_id": int(user_id) if user_id is not None else None,
            "cancel_reason": str(reason or "activation cancelled")[:500],
            "updated_at": _iso(current_time),
            "next_attempt_at": None,
        }
    )
    _write_intent(role, intent)
    # Turn on authorizes the outer paid task-generation outbox. Cancelling only
    # this nested intent would leave a previously accepted/recoverable broker
    # delivery free to generate after Turn off. Restore the publish-time hold
    # and invalidate any running claim; a later Turn on can authorize it again.
    from .task_provisioning_state import (
        defer_assessment_task_provisioning_until_activation,
    )

    defer_assessment_task_provisioning_until_activation(
        role,
        reason=str(reason or "activation cancelled"),
        now=current_time,
    )
    return True


def complete_role_activation_intent(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    worker_task_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Complete Turn on through the lock-free external-preparation workflow."""
    from .role_activation_repository_completion import (
        complete_role_activation_intent as complete_activation,
    )

    return complete_activation(
        db, role_id=role_id, request_id=request_id,
        worker_task_id=worker_task_id, now=now,
    )

__all__ = [
    "ACTIVATION_ACTIVE_STATUSES",
    "ACTIVATION_BLOCKED",
    "ACTIVATION_CANCELLED",
    "ACTIVATION_PENDING",
    "ACTIVATION_RETRY_WAIT",
    "ACTIVATION_SUCCEEDED",
    "activation_intent_is_due",
    "activation_intent_state",
    "activation_intent_task_ready",
    "block_activation_intent_for_unavailable_selected_task",
    "block_activation_intent_if_task_exhausted",
    "cancel_role_activation_intent",
    "complete_role_activation_intent",
    "refresh_role_activation_intent_policy",
    "request_role_activation_intent",
]
