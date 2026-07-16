"""Lock-free external preflight for the synchronous Role Turn-on PATCH."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.role import Role
from ...models.user import User
from ...services.agent_activation_readiness import (
    activation_readiness,
    readiness_message,
)
from ...services.agent_policy_settings import activation_policy_values
from ...services.role_activation_command import (
    ExplicitAssessmentChoiceRequired,
    lock_generated_task_for_activation,
    resolve_activation_assessment_action,
)
from ...services.role_concurrency import assert_role_version
from ...services.task_approval_service import (
    PreparedTaskApproval,
    TaskApprovalError,
    capture_task_approval,
    prepare_task_approval,
    apply_prepared_task_approval,
)
from ...services.task_repository_serialization import (
    task_repository_write_mutex,
)
from .job_authorization import JobPermission, require_job_permission


@dataclass
class DirectActivationPreparation:
    """External results plus an optional repository mutex held through commit."""

    task_id: int | None = None
    task_approval: PreparedTaskApproval | None = None
    _repository_mutex: Any | None = field(default=None, repr=False)
    _released: bool = field(default=False, repr=False)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._repository_mutex is not None:
            self._repository_mutex.__exit__(None, None, None)


def _authorized_role(
    db: Session,
    *,
    current_user: User,
    role_id: int,
    expected_version: int,
) -> Role:
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
        lock_for_update=False,
    )
    assert_role_version(role, expected_version=expected_version)
    return role


def _activation_action(role: Role, updates: dict[str, Any]) -> str | None:
    try:
        return resolve_activation_assessment_action(
            role,
            updates,
            updates.get("activation_assessment_action"),
        )
    except ExplicitAssessmentChoiceRequired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _generated_draft(role: Role) -> Any:
    drafts = [
        task
        for task in list(role.tasks or [])
        if not bool(task.is_active)
        and isinstance(task.extra_data, dict)
        and task.extra_data.get("generated")
        and task.extra_data.get("needs_review", True)
    ]
    if len(drafts) != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Turn on can approve exactly one linked generated draft; wait "
                "for generation or resolve multiple drafts before retrying."
            ),
        )
    return drafts[0]


def _require_passing_battle_test(task: Any) -> None:
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    if (extra.get("battle_test") or {}).get("verdict") != "pass":
        raise HTTPException(
            status_code=409,
            detail=(
                "The generated assessment cannot be approved until its "
                "automated battle test passes. Choose Skip assessment or retry "
                "after validation completes."
            ),
        )


def _external_readiness(
    db: Session,
    *,
    role: Role,
    updates: dict[str, Any],
    incoming_budget: int,
    action: str | None,
    preview_active_task_id: int | None,
) -> None:
    policy = activation_policy_values(role, updates)
    readiness = activation_readiness(
        role,
        auto_skip_assessment=(
            True
            if action == "skip_assessment"
            else (
                bool(updates["auto_skip_assessment"])
                if updates.get("auto_skip_assessment") is not None
                else None
            )
        ),
        monthly_usd_budget_cents=incoming_budget,
        auto_send_assessment=policy["auto_send_assessment"],
        auto_resend_assessment=policy["auto_resend_assessment"],
        auto_advance=policy["auto_advance"],
        auto_reject=(
            bool(updates["auto_reject"])
            if updates.get("auto_reject") is not None
            else None
        ),
        auto_reject_pre_screen=(
            bool(updates["auto_reject_pre_screen"])
            if updates.get("auto_reject_pre_screen") is not None
            else None
        ),
        preview_active_task_id=preview_active_task_id,
    )
    if not readiness.get("ready"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Agent runtime is not ready: "
                f"{readiness_message(readiness)}. Turn on was not applied."
            ),
        )


def prepare_direct_role_activation(
    db: Session,
    *,
    current_user: User,
    role_id: int,
    expected_version: int,
    updates: dict[str, Any],
) -> DirectActivationPreparation | None:
    """Run worker/provider checks with no Role or Task row lock held."""

    if updates.get("agentic_mode_enabled") is not True:
        return None
    role = _authorized_role(
        db,
        current_user=current_user,
        role_id=role_id,
        expected_version=expected_version,
    )
    action = _activation_action(role, updates)
    if action == "approve_when_ready":
        db.rollback()
        return None
    if action and bool(role.agentic_mode_enabled):
        db.rollback()
        raise HTTPException(status_code=409, detail="The agent is already enabled")
    incoming_budget = updates.get(
        "monthly_usd_budget_cents",
        role.monthly_usd_budget_cents,
    )
    if incoming_budget is None or int(incoming_budget) <= 0:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail="monthly_usd_budget_cents is required to enable agentic mode",
        )

    selected_task_id = None
    if action == "approve_generated_task":
        selected_task_id = int(_generated_draft(role).id)
    db.rollback()

    preparation = DirectActivationPreparation(task_id=selected_task_id)
    if selected_task_id is not None:
        mutex = task_repository_write_mutex(db, task_id=selected_task_id)
        try:
            mutex.__enter__()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="The task repository is busy or unavailable; retry Turn on.",
            ) from exc
        preparation._repository_mutex = mutex

    try:
        # Reauthorize/reload after waiting for the repository mutex. A task edit
        # or assignment change that won the mutex must be observed before capture.
        role = _authorized_role(
            db,
            current_user=current_user,
            role_id=role_id,
            expected_version=expected_version,
        )
        action = _activation_action(role, updates)
        captured = None
        if selected_task_id is not None:
            task = _generated_draft(role)
            if int(task.id) != selected_task_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The generated assessment changed while Turn on was "
                        "being prepared. Refresh the job and retry."
                    ),
                )
            _require_passing_battle_test(task)
            captured = capture_task_approval(task)

        _external_readiness(
            db,
            role=role,
            updates=updates,
            incoming_budget=int(incoming_budget),
            action=action,
            preview_active_task_id=selected_task_id,
        )
        db.rollback()
        if captured is not None:
            try:
                preparation.task_approval = prepare_task_approval(captured)
            except TaskApprovalError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The generated assessment repository is not ready; "
                        f"Turn on was not applied: {exc.public_detail}"
                    ),
                ) from exc
        return preparation
    except Exception:
        db.rollback()
        preparation.release()
        raise


def apply_prepared_direct_activation_task(
    db: Session,
    *,
    role: Role,
    preparation: DirectActivationPreparation | None,
    organization_id: int,
    user_id: int,
) -> int:
    """Apply the exact prepared draft after the caller locked the Role."""

    draft = _generated_draft(role)
    locked = lock_generated_task_for_activation(
        db,
        task_id=int(draft.id),
        organization_id=organization_id,
    )
    if locked is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "The generated assessment changed while Turn on was being "
                "applied. Refresh the job and retry."
            ),
        )
    if (
        preparation is None
        or preparation.task_approval is None
        or preparation.task_id != int(locked.id)
    ):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Turn on preflight became stale. Refresh the job and retry.",
        )
    try:
        apply_prepared_task_approval(
            db,
            locked,
            preparation.task_approval,
            user_id=user_id,
            approval_role_id=int(role.id),
        )
    except TaskApprovalError as exc:
        db.rollback()
        raise HTTPException(
            status_code=(
                409
                if exc.code
                in {"task_approval_superseded", "task_shared_approval_scope"}
                else 503
            ),
            detail=exc.public_detail,
        ) from exc
    return int(locked.id)


__all__ = [
    "DirectActivationPreparation",
    "apply_prepared_direct_activation_task",
    "prepare_direct_role_activation",
]
