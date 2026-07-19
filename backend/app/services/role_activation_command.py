"""Focused helpers for role Turn-on validation and recovery boundaries."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..models.role import Role
from ..models.task import Task
from .agent_policy_settings import activation_policy_values


_COMPENSATION_FIELDS = (
    "agentic_mode_enabled",
    "agent_paused_at",
    "agent_paused_reason",
    "auto_promote",
    "auto_send_assessment",
    "auto_resend_assessment",
    "auto_advance",
    "auto_reject",
    "auto_reject_pre_screen",
    "auto_skip_assessment",
    "monthly_usd_budget_cents",
    "agent_action_allowlist",
    "agent_token_budget_per_cycle",
    "agent_decision_budget_per_cycle",
    "score_threshold",
    "auto_reject_threshold_mode",
    "starred_for_auto_sync",
    "job_status",
    "assessment_task_provisioning",
)


class ExplicitAssessmentChoiceRequired(RuntimeError):
    """Turn-on cannot infer Generate versus Skip for a taskless role."""


@dataclass(frozen=True)
class ActivationDispatchCompensation:
    detail: str
    role: Role | None
    role_activation_compensated: bool


def lock_generated_task_for_activation(
    db,
    *,
    task_id: int,
    organization_id: int,
) -> Task | None:
    """Lock a still-reviewable generated draft selected by Turn on."""

    draft = (
        db.query(Task)
        .filter(
            Task.id == int(task_id),
            Task.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=Task)
        .one_or_none()
    )
    if draft is None:
        return None
    extra = draft.extra_data if isinstance(draft.extra_data, dict) else {}
    if (
        bool(draft.is_active)
        or not bool(extra.get("generated"))
        or not bool(extra.get("needs_review", True))
    ):
        return None
    return draft


def has_blocked_role_reconfiguration(role: Role) -> bool:
    provisioning = (
        role.assessment_task_provisioning
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    reconfiguration = provisioning.get("reconfiguration")
    return bool(
        isinstance(reconfiguration, dict)
        and str(reconfiguration.get("status") or "") == "blocked"
    )


def resolve_activation_assessment_action(
    role: Role,
    updates: dict[str, Any],
    action: str | None,
) -> str | None:
    """Preserve explicit HITL choice while unblocking confirmed republishes."""
    turning_on = bool(updates.get("agentic_mode_enabled")) and not bool(
        role.agentic_mode_enabled
    )
    if action is not None or not turning_on:
        return action
    if has_blocked_role_reconfiguration(role):
        return "approve_when_ready"
    configured_skip = updates.get(
        "auto_skip_assessment", role.auto_skip_assessment
    )
    has_active_task = any(bool(task.is_active) for task in role.tasks or [])
    if not bool(configured_skip) and not has_active_task:
        raise ExplicitAssessmentChoiceRequired(
            "Choose Generate assessment or Skip assessment before turning on."
        )
    return None


def apply_durable_activation_policy(
    role: Role,
    updates: dict[str, Any],
) -> dict[str, bool]:
    """Apply UI policy fields before persisting a durable Turn-on snapshot."""
    if updates.get("auto_reject") is not None:
        role.auto_reject = bool(updates["auto_reject"])
    if updates.get("auto_reject_pre_screen") is not None:
        role.auto_reject_pre_screen = bool(updates["auto_reject_pre_screen"])
    if updates.get("auto_skip_assessment") is not None:
        role.auto_skip_assessment = bool(updates["auto_skip_assessment"])
    for field in (
        "agent_action_allowlist",
        "agent_token_budget_per_cycle",
        "agent_decision_budget_per_cycle",
        "score_threshold",
    ):
        if field in updates:
            setattr(role, field, updates[field])
    if updates.get("auto_reject_threshold_mode") is not None:
        role.auto_reject_threshold_mode = str(updates["auto_reject_threshold_mode"])
    return activation_policy_values(role, updates)


def capture_activation_compensation_state(role: Role) -> dict[str, Any]:
    return {
        field: copy.deepcopy(getattr(role, field, None))
        for field in _COMPENSATION_FIELDS
    }


def restore_activation_compensation_state(
    role: Role,
    state: dict[str, Any],
) -> None:
    for field in _COMPENSATION_FIELDS:
        setattr(role, field, copy.deepcopy(state[field]))


def compensate_failed_activation_dispatch(
    db,
    *,
    role_id: int,
    organization_id: int,
    dispatched_role_version: int,
    agent_activated_now: bool,
    activation_previous: dict[str, Any],
    activation_approved_task_id: int | None,
    actor_user_id: int,
    request_id: str | None,
) -> ActivationDispatchCompensation:
    """Exact-CAS compensation for a broker-rejected activation/resume.

    Repository preparation is useful durable work, so an approved task stays
    active. The Role transition is restored only when its dispatch revision is
    still current; a newer collaborator save is never overwritten.
    """

    from ..agent_runtime import budget_guard
    from .agent_activation_checklist import resolve_satisfied_activation_questions
    from .role_change_audit import (
        add_role_change_event,
        capture_role_change_snapshot,
    )
    from .role_concurrency import bump_role_version, role_query_for_update
    from .workspace_agent_control import workspace_agent_control_snapshot

    workspace_agent_control_snapshot(
        db,
        organization_id=int(organization_id),
        lock=True,
    )
    compensation_role = (
        role_query_for_update(
            db,
            role_id=int(role_id),
            organization_id=int(organization_id),
        )
        .populate_existing()
        .first()
    )
    can_compensate = bool(
        compensation_role is not None
        and int(compensation_role.version or 1) == int(dispatched_role_version)
        and bool(compensation_role.agentic_mode_enabled)
        and (
            bool(agent_activated_now)
            or compensation_role.agent_paused_at is None
        )
    )
    role_activation_compensated = bool(can_compensate and agent_activated_now)
    if can_compensate and compensation_role is not None:
        compensation_before = capture_role_change_snapshot(compensation_role)
        compensation_from = int(compensation_role.version or 1)
        if agent_activated_now:
            restore_activation_compensation_state(
                compensation_role,
                activation_previous,
            )
            if activation_approved_task_id is not None:
                provisioning = (
                    dict(compensation_role.assessment_task_provisioning)
                    if isinstance(
                        compensation_role.assessment_task_provisioning,
                        dict,
                    )
                    else {}
                )
                provisioning["last_activation_attempt"] = {
                    "status": "task_prepared_bootstrap_failed",
                    "task_id": int(activation_approved_task_id),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                compensation_role.assessment_task_provisioning = provisioning
        else:
            budget_guard.pause_role(
                db,
                role=compensation_role,
                reason="agent bootstrap dispatch failed",
            )
        compensation_role.agent_bootstrap_status = "failed"
        compensation_role.agent_bootstrap_error = (
            "agent bootstrap dispatch failed after assessment preparation"
            if activation_approved_task_id is not None
            else "agent bootstrap dispatch failed"
        )
        compensation_role.agent_bootstrap_completed_at = datetime.now(timezone.utc)
        compensation_to = bump_role_version(compensation_role)
        add_role_change_event(
            db,
            role=compensation_role,
            before=compensation_before,
            action="agent_bootstrap_compensated",
            actor_user_id=int(actor_user_id),
            from_version=compensation_from,
            to_version=compensation_to,
            reason=(
                "agent bootstrap dispatch failed after assessment task "
                "preparation; prepared task retained"
                if activation_approved_task_id is not None
                else "agent bootstrap dispatch failed"
            ),
            request_id=request_id,
        )
    if activation_approved_task_id is not None and compensation_role is not None:
        resolve_satisfied_activation_questions(db, role=compensation_role)

    if activation_approved_task_id is None:
        detail = (
            "The agent could not be started because the worker queue is "
            "unavailable. Its latest shared state was preserved; refresh the "
            "job before retrying."
        )
    elif role_activation_compensated:
        detail = (
            "The assessment was prepared successfully and remains ready, but "
            "the agent could not be started because the worker queue is "
            "unavailable. The role was restored to off; refresh the job and "
            "retry Turn on."
        )
    else:
        detail = (
            "The assessment was prepared successfully and remains ready. The "
            "worker queue rejected this bootstrap, and newer shared role state "
            "was preserved; refresh the job before retrying."
        )
    return ActivationDispatchCompensation(
        detail=detail,
        role=compensation_role,
        role_activation_compensated=role_activation_compensated,
    )


def resolve_reconfiguration_as_skipped(
    role: Role,
    *,
    user_id: int | None,
    now: datetime | None = None,
) -> bool:
    provisioning = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    reconfiguration = provisioning.get("reconfiguration")
    if not isinstance(reconfiguration, dict) or str(
        reconfiguration.get("status") or ""
    ) not in {"blocked", "pending", "running"}:
        return False
    current_time = now or datetime.now(timezone.utc)
    provisioning["reconfiguration"] = {
        **reconfiguration,
        "status": "succeeded",
        "resolution": "assessment_skipped_by_user",
        "resolved_by_user_id": int(user_id) if user_id is not None else None,
        "last_error": None,
        "completed_at": current_time.isoformat(),
        "updated_at": current_time.isoformat(),
    }
    role.assessment_task_provisioning = provisioning
    return True


__all__ = [
    "ActivationDispatchCompensation",
    "ExplicitAssessmentChoiceRequired",
    "apply_durable_activation_policy",
    "capture_activation_compensation_state",
    "compensate_failed_activation_dispatch",
    "has_blocked_role_reconfiguration",
    "lock_generated_task_for_activation",
    "resolve_reconfiguration_as_skipped",
    "resolve_activation_assessment_action",
    "restore_activation_compensation_state",
]
