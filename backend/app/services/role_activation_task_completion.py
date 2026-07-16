"""Two-phase task approval guards for durable role activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from ..models.task import Task
from .role_activation_task_guard import (
    activation_task_reference,
    intent_task,
    lock_activation_task,
    record_activation_retry,
)
from .task_approval_service import (
    CapturedTaskApproval,
    PreparedTaskApproval,
    TaskApprovalError,
    apply_prepared_task_approval,
    capture_task_approval,
    task_approval_fingerprint,
)


@dataclass(frozen=True)
class ActivationTaskPreparation:
    """Exact task state authorized before provider work starts."""

    task_id: int | None
    fingerprint: str | None
    captured_approval: CapturedTaskApproval | None


def capture_activation_task_for_completion(
    db: Session,
    *,
    role: Role,
    intent: dict[str, Any],
    request_id: str,
    now: datetime,
    skip_assessment: bool,
) -> tuple[ActivationTaskPreparation | None, dict[str, Any] | None]:
    """Lock and capture the exact task without doing repository I/O."""

    reference = activation_task_reference(role, intent)
    task = intent_task(role, intent)
    if task is not None:
        task = lock_activation_task(db, role=role, intent=intent)
    if task is None and not skip_assessment:
        if reference.invalid:
            return None, record_activation_retry(
                db,
                role_id=int(role.id),
                request_id=request_id,
                error=(
                    "The assessment task selected for Turn on has an invalid "
                    "identifier. Select or generate a task again, or skip the "
                    "assessment stage, then press Turn on again."
                ),
                now=now,
                blocked=True,
            )
        if reference.task_id is not None:
            return None, record_activation_retry(
                db,
                role_id=int(role.id),
                request_id=request_id,
                error=(
                    "The assessment task selected for Turn on is no longer "
                    "linked and active. Select or generate another task, or "
                    "skip the assessment stage, then press Turn on again."
                ),
                now=now,
                blocked=True,
            )
        return None, {"status": "waiting_for_task"}

    extra = task.extra_data if task is not None and isinstance(task.extra_data, dict) else {}
    if not skip_assessment and task is not None and not bool(task.is_active):
        if not bool(extra.get("generated")) or not bool(
            extra.get("needs_review", True)
        ):
            return None, record_activation_retry(
                db,
                role_id=int(role.id),
                request_id=request_id,
                error=(
                    "The assessment task selected for Turn on changed and is no "
                    "longer an approvable generated draft. Review or replace the "
                    "task, then press Turn on again."
                ),
                now=now,
                blocked=True,
            )
        battle = extra.get("battle_test") if isinstance(extra.get("battle_test"), dict) else {}
        if battle.get("verdict") != "pass":
            battle_state = extra.get("battle_test_provisioning") or {}
            if str(battle_state.get("status") or "") == "repair_exhausted":
                return None, record_activation_retry(
                    db,
                    role_id=int(role.id),
                    request_id=request_id,
                    error="automated assessment repair was exhausted",
                    now=now,
                    blocked=True,
                )
            return None, {"status": "waiting_for_battle_test"}
    return (
        ActivationTaskPreparation(
            task_id=int(task.id) if task is not None else None,
            fingerprint=(
                task_approval_fingerprint(task) if task is not None else None
            ),
            captured_approval=(
                capture_task_approval(task)
                if task is not None and not bool(task.is_active)
                else None
            ),
        ),
        None,
    )


def apply_activation_task_preparation(
    db: Session,
    *,
    role: Role,
    intent: dict[str, Any],
    preparation: ActivationTaskPreparation,
    prepared_approval: PreparedTaskApproval | None,
) -> Task | None:
    """Re-lock and apply only the task captured before provider work."""

    if preparation.task_id is None:
        return None
    task = lock_activation_task(db, role=role, intent=intent)
    if task is None or int(task.id) != preparation.task_id:
        raise TaskApprovalError(
            "The activation task was unlinked or replaced during readiness",
            code="task_approval_superseded",
            public_message=(
                "The selected assessment task changed while Turn on was being "
                "prepared. The latest task will be checked on the next retry."
            ),
        )
    if task_approval_fingerprint(task) != preparation.fingerprint:
        raise TaskApprovalError(
            f"Task {task.id} changed during activation readiness",
            code="task_approval_superseded",
            public_message=(
                "The selected assessment task changed while Turn on was being "
                "prepared. The latest task will be checked on the next retry."
            ),
        )
    if prepared_approval is not None:
        apply_prepared_task_approval(
            db,
            task,
            prepared_approval,
            user_id=(
                int(intent["requested_by_user_id"])
                if intent.get("requested_by_user_id") is not None
                else None
            ),
            approval_role_id=int(role.id),
        )
        from .agent_activation_checklist import (
            resolve_satisfied_activation_questions,
        )

        resolve_satisfied_activation_questions(db, role=role)
    return task


__all__ = [
    "ActivationTaskPreparation",
    "apply_activation_task_preparation",
    "capture_activation_task_for_completion",
]
