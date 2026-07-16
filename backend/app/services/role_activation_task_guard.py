"""Task selection and fail-closed state guards for durable role activation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.role import Role, role_tasks
from ..models.task import Task

logger = logging.getLogger("taali.role_activation_intent")

ACTIVATION_PENDING = "pending"
ACTIVATION_RETRY_WAIT = "retry_wait"
ACTIVATION_BLOCKED = "blocked"
ACTIVATION_SUCCEEDED = "succeeded"
ACTIVATION_CANCELLED = "cancelled"
ACTIVATION_ACTIVE_STATUSES = frozenset(
    {ACTIVATION_PENDING, ACTIVATION_RETRY_WAIT}
)
ACTIVATION_POLICY_MUTABLE_STATUSES = frozenset(
    {*ACTIVATION_ACTIVE_STATUSES, ACTIVATION_BLOCKED}
)


@dataclass(frozen=True)
class ActivationTaskReference:
    task_id: int | None
    source: str | None
    invalid: bool = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime) -> str:
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


def activation_intent_state(role: Role) -> dict[str, Any]:
    provisioning = getattr(role, "assessment_task_provisioning", None)
    if not isinstance(provisioning, dict):
        return {}
    intent = provisioning.get("activation_intent")
    return dict(intent) if isinstance(intent, dict) else {}


def write_activation_intent(role: Role, intent: dict[str, Any]) -> None:
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    provisioning["activation_intent"] = intent
    role.assessment_task_provisioning = provisioning


def activation_intent_is_due(
    role: Role,
    *,
    now: datetime | None = None,
) -> bool:
    intent = activation_intent_state(role)
    status = str(intent.get("status") or "")
    if status == ACTIVATION_PENDING:
        return True
    if status != ACTIVATION_RETRY_WAIT:
        return False
    next_attempt_at = _parse_time(intent.get("next_attempt_at"))
    return next_attempt_at is None or next_attempt_at <= (now or utcnow())


def lock_activation_role(
    db: Session,
    *,
    role_id: int,
    fail_if_workspace_paused: bool,
) -> tuple[Role | None, bool]:
    """Take activation authority in canonical Organization -> Role order."""
    role_identity = (
        db.query(Role.organization_id)
        .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
        .one_or_none()
    )
    if role_identity is None:
        return None, False
    from .workspace_agent_control import workspace_agent_control_snapshot

    workspace_paused, _workspace_version = workspace_agent_control_snapshot(
        db,
        organization_id=int(role_identity.organization_id),
        lock=True,
    )
    if workspace_paused and fail_if_workspace_paused:
        db.rollback()
        return None, True
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(role_identity.organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    return role, workspace_paused


def record_activation_retry(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    error: str,
    now: datetime,
    blocked: bool = False,
) -> dict[str, Any]:
    """Persist a retry/block cursor under the same activation lock order."""
    db.rollback()
    role, _workspace_paused = lock_activation_role(
        db,
        role_id=role_id,
        fail_if_workspace_paused=False,
    )
    if role is None:
        return {"status": "missing"}
    intent = activation_intent_state(role)
    if str(intent.get("request_id") or "") != str(request_id):
        db.rollback()
        return {"status": "superseded"}
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        db.rollback()
        return {"status": str(intent.get("status") or "inactive")}
    status = ACTIVATION_BLOCKED if blocked else ACTIVATION_RETRY_WAIT
    intent.update(
        {
            "status": status,
            "attempts": int(intent.get("attempts") or 0) + 1,
            "last_error": str(error or "activation failed")[:2000],
            "next_attempt_at": (
                None if blocked else iso_time(now + timedelta(minutes=5))
            ),
            "updated_at": iso_time(now),
        }
    )
    write_activation_intent(role, intent)
    db.commit()
    return {"status": status, "reason": intent["last_error"]}


def activation_task_reference(
    role: Role,
    intent: dict[str, Any],
) -> ActivationTaskReference:
    requested_task_id = intent.get("task_id")
    source = "activation_intent" if requested_task_id is not None else None
    provisioning = (
        role.assessment_task_provisioning
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    if requested_task_id is None:
        requested_task_id = provisioning.get("task_id")
        source = "provisioning" if requested_task_id is not None else None
    if requested_task_id is None:
        return ActivationTaskReference(task_id=None, source=None)
    parsed = None
    if isinstance(requested_task_id, int) and not isinstance(
        requested_task_id, bool
    ):
        parsed = requested_task_id
    elif isinstance(requested_task_id, str) and requested_task_id.strip().isdigit():
        parsed = int(requested_task_id.strip())
    if parsed is None or parsed <= 0:
        logger.error(
            "invalid activation task id role_id=%s value=%r",
            getattr(role, "id", None),
            requested_task_id,
        )
        return ActivationTaskReference(
            task_id=None,
            source=source,
            invalid=True,
        )
    return ActivationTaskReference(task_id=parsed, source=source)


def selected_activation_task_id(
    role: Role,
    intent: dict[str, Any],
) -> int | None:
    return activation_task_reference(role, intent).task_id


def intent_task(role: Role, intent: dict[str, Any]):
    reference = activation_task_reference(role, intent)
    if reference.invalid:
        return None
    requested_task_id = reference.task_id
    drafts = []
    eligible = []
    for task in list(getattr(role, "tasks", None) or []):
        extra = task.extra_data if isinstance(task.extra_data, dict) else {}
        if bool(task.is_active):
            eligible.append(task)
        if (
            not bool(task.is_active)
            and extra.get("generated")
            and extra.get("needs_review", True)
        ):
            drafts.append(task)
            eligible.append(task)
    if requested_task_id is not None:
        return next(
            (task for task in eligible if int(task.id) == requested_task_id),
            None,
        )
    if len(drafts) == 1:
        return drafts[0]
    active = [task for task in eligible if bool(task.is_active)]
    return active[0] if len(active) == 1 else None


def lock_activation_task(
    db: Session,
    *,
    role: Role,
    intent: dict[str, Any],
) -> Task | None:
    """Reload and lock the selected linked Task after the canonical Role lock."""

    selected = intent_task(role, intent)
    if selected is None:
        return None
    return (
        db.query(Task)
        .join(role_tasks, role_tasks.c.task_id == Task.id)
        .filter(
            role_tasks.c.role_id == int(role.id),
            Task.id == int(selected.id),
            or_(
                Task.organization_id == int(role.organization_id),
                and_(Task.organization_id.is_(None), Task.is_template.is_(True)),
            ),
        )
        .populate_existing()
        .with_for_update(of=Task)
        .one_or_none()
    )


def activation_intent_task_ready(role: Role) -> bool:
    if not activation_intent_is_due(role):
        return False
    intent = activation_intent_state(role)
    if bool(getattr(role, "auto_skip_assessment", False)):
        return True
    task = intent_task(role, intent)
    if task is None:
        return False
    if bool(task.is_active):
        return True
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    return (extra.get("battle_test") or {}).get("verdict") == "pass"


def block_activation_intent_if_task_exhausted(
    role: Role,
    *,
    now: datetime | None = None,
) -> bool:
    """Surface the bounded auto-repair terminal state without worker dispatch."""
    intent = activation_intent_state(role)
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        return False
    reference = activation_task_reference(role, intent)
    if reference.invalid:
        current_time = now or utcnow()
        intent.update(
            {
                "status": ACTIVATION_BLOCKED,
                "last_error": (
                    "The assessment task selected for Turn on has an invalid "
                    "identifier. Select or generate a task again, or skip the "
                    "assessment stage, then press Turn on again."
                ),
                "next_attempt_at": None,
                "blocked_at": iso_time(current_time),
                "updated_at": iso_time(current_time),
            }
        )
        write_activation_intent(role, intent)
        return True
    task = intent_task(role, intent)
    if task is None:
        return False
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    battle_state = extra.get("battle_test_provisioning") or {}
    if str(battle_state.get("status") or "") != "repair_exhausted":
        return False
    current_time = now or utcnow()
    intent.update(
        {
            "status": ACTIVATION_BLOCKED,
            "task_id": int(task.id),
            "last_error": (
                "Automated assessment repair was exhausted. Update the job "
                "specification and press Turn on again, or explicitly skip "
                "the assessment stage."
            ),
            "next_attempt_at": None,
            "blocked_at": iso_time(current_time),
            "updated_at": iso_time(current_time),
        }
    )
    write_activation_intent(role, intent)
    return True


def block_activation_intent_for_unavailable_selected_task(
    role: Role,
    *,
    task_id: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Fail closed when the exact task authorized by Turn on disappears."""
    intent = activation_intent_state(role)
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        return False
    selected_task_id = selected_activation_task_id(role, intent)
    if selected_task_id is None:
        return False
    if task_id is not None and int(task_id) != selected_task_id:
        return False

    current_time = now or utcnow()
    intent.update(
        {
            "status": ACTIVATION_BLOCKED,
            "task_id": selected_task_id,
            "last_error": str(
                reason
                or (
                    "The assessment task selected for Turn on is no longer "
                    "linked and active. Select or generate another task, or "
                    "skip the assessment stage, then press Turn on again."
                )
            )[:2000],
            "next_attempt_at": None,
            "blocked_at": iso_time(current_time),
            "updated_at": iso_time(current_time),
        }
    )
    write_activation_intent(role, intent)
    return True


__all__ = [
    "ActivationTaskReference",
    "ACTIVATION_ACTIVE_STATUSES",
    "ACTIVATION_BLOCKED",
    "ACTIVATION_CANCELLED",
    "ACTIVATION_PENDING",
    "ACTIVATION_POLICY_MUTABLE_STATUSES",
    "ACTIVATION_RETRY_WAIT",
    "ACTIVATION_SUCCEEDED",
    "activation_intent_is_due",
    "activation_intent_state",
    "activation_intent_task_ready",
    "activation_task_reference",
    "block_activation_intent_for_unavailable_selected_task",
    "block_activation_intent_if_task_exhausted",
    "intent_task",
    "iso_time",
    "lock_activation_role",
    "lock_activation_task",
    "record_activation_retry",
    "selected_activation_task_id",
    "utcnow",
    "write_activation_intent",
]
