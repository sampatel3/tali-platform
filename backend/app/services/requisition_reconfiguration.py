"""Fail-closed reconfiguration for a changed, already-running requisition."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role, role_tasks
from ..platform.config import settings
from .agent_policy_settings import role_automation_enabled
from .role_activation_intent import request_role_activation_intent
from .task_provisioning_service import request_assessment_task_provisioning


@dataclass(frozen=True)
class RequisitionReconfiguration:
    changed: bool
    status: str
    dispatch_generation: bool = False
    superseded_task_id: int | None = None
    detail: str | None = None


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def _safe_generated_task(db: Session, role: Role):
    """Return the exact task proven to belong to the last durable activation."""

    tasks = list(getattr(role, "tasks", None) or [])
    if len(tasks) != 1:
        return None
    task = tasks[0]
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    state = (
        role.assessment_task_provisioning
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    intent = state.get("activation_intent")
    if not isinstance(intent, dict):
        return None
    try:
        activated_task_id = int(intent["task_id"])
    except (KeyError, TypeError, ValueError):
        return None
    linked_roles = (
        db.query(role_tasks.c.role_id)
        .filter(role_tasks.c.task_id == int(task.id))
        .all()
    )
    if not (
        bool(getattr(task, "is_active", False))
        and bool(extra.get("generated"))
        and not bool(extra.get("needs_review", True))
        and str(intent.get("status") or "") == "succeeded"
        and activated_task_id == int(task.id)
        # A generated artifact is safe to retire automatically only when it is
        # exclusive to this role; a shared task would require HITL because
        # deactivating it changes another role's execution path.
        and len(linked_roles) == 1
        and int(linked_roles[0][0]) == int(role.id)
    ):
        return None
    return task


def _take_role_off_for_reconfiguration(role: Role) -> None:
    role.agentic_mode_enabled = False
    # Starred Workable imports auto-score newly created applications even while
    # agent mode is off. Clear it until durable activation restores the star.
    role.starred_for_auto_sync = False
    if role.source == "requisition" and role.job_status == JOB_STATUS_OPEN:
        # Native intake consults job_status. This also hides the role from the
        # careers board while its candidate-facing task is being replaced.
        role.job_status = JOB_STATUS_DRAFT


def _block_for_task_review(
    role: Role,
    *,
    user_id: int,
    target_fingerprint: str,
    detail: str,
    now: datetime,
) -> RequisitionReconfiguration:
    _take_role_off_for_reconfiguration(role)
    request_id = uuid.uuid4().hex
    state = (
        dict(role.assessment_task_provisioning)
        if isinstance(role.assessment_task_provisioning, dict)
        else {}
    )
    linked_ids = [int(task.id) for task in list(getattr(role, "tasks", None) or [])]
    intent = {
        "command": "review_republished_task",
        "status": "blocked",
        "request_id": request_id,
        "task_id": linked_ids[0] if len(linked_ids) == 1 else None,
        "monthly_usd_budget_cents": int(role.monthly_usd_budget_cents or 0),
        "auto_promote": bool(role.auto_promote),
        "auto_send_assessment": role_automation_enabled(
            role, "auto_send_assessment"
        ),
        "auto_resend_assessment": role_automation_enabled(
            role, "auto_resend_assessment"
        ),
        "auto_advance": role_automation_enabled(role, "auto_advance"),
        "requested_by_user_id": int(user_id),
        "requested_at": _iso(now),
        "updated_at": _iso(now),
        "last_error": detail[:2000],
        "next_attempt_at": None,
        "blocked_at": _iso(now),
    }
    state.update(
        {
            "status": "blocked",
            "reason": "requisition_republish_task_review",
            "last_error": detail[:2000],
            "next_attempt_at": None,
            # Invalidate any generation worker that claimed the prior role
            # intent before this republish. It rechecks this token immediately
            # before persisting provider output.
            "claim_token": None,
            "claimed_at": None,
            "updated_at": _iso(now),
            "activation_intent": intent,
            "reconfiguration": {
                "status": "blocked",
                "request_id": request_id,
                "target_role_intent_fingerprint": target_fingerprint,
                "preserved_task_ids": linked_ids,
                "requested_by_user_id": int(user_id),
                "requested_at": _iso(now),
                "updated_at": _iso(now),
                "last_error": detail[:2000],
            },
        }
    )
    role.assessment_task_provisioning = state
    # ``agent_bootstrap_status`` is a stable API enum (starting|ready|failed).
    # The richer provisioning payload above carries the actionable HITL state.
    role.agent_bootstrap_status = "failed"
    role.agent_bootstrap_error = detail[:2000]
    role.agent_bootstrap_completed_at = now
    return RequisitionReconfiguration(
        changed=True,
        status="blocked",
        detail=detail,
    )


def prepare_running_role_reconfiguration(
    db: Session,
    *,
    role: Role,
    user_id: int,
    target_fingerprint: str,
    now: datetime | None = None,
) -> RequisitionReconfiguration:
    """Replace a prior auto-generated task and durably resume after republish.

    The caller owns the already-locked Role transaction. No external work is
    performed here; generation is kicked only after that transaction commits.
    """

    current_time = now or datetime.now(timezone.utc)
    if getattr(role, "agent_paused_at", None) is not None:
        return _block_for_task_review(
            role,
            user_id=user_id,
            target_fingerprint=target_fingerprint,
            detail=(
                "The requisition changed while the agent was manually paused. "
                "The role was left off; review the assessment choice and resume explicitly."
            ),
            now=current_time,
        )
    if not bool(getattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False)):
        return _block_for_task_review(
            role,
            user_id=user_id,
            target_fingerprint=target_fingerprint,
            detail=(
                "The requisition changed while the agent was on, but automatic "
                "assessment generation is disabled. The role was left off for task review."
            ),
            now=current_time,
        )
    if int(getattr(role, "monthly_usd_budget_cents", 0) or 0) <= 0:
        return _block_for_task_review(
            role,
            user_id=user_id,
            target_fingerprint=target_fingerprint,
            detail=(
                "The requisition changed while the agent was on, but its prior "
                "authorized budget is missing. The role was left off for review."
            ),
            now=current_time,
        )

    task = _safe_generated_task(db, role)
    if task is None:
        return _block_for_task_review(
            role,
            user_id=user_id,
            target_fingerprint=target_fingerprint,
            detail=(
                "The requisition changed while the agent was on. Linked assessment "
                "tasks were preserved because they are manual or their automatic "
                "provenance is ambiguous. Review/confirm the task choice, then press Turn on."
            ),
            now=current_time,
        )

    previous_task_id = int(task.id)
    previous_extra = dict(task.extra_data or {})
    previous_extra.update(
        {
            "superseded": True,
            "superseded_at": _iso(current_time),
            "superseded_reason": "requisition_republished_while_running",
            "replacement_role_intent_fingerprint": target_fingerprint,
            "needs_review": False,
        }
    )
    battle_state = (
        dict(previous_extra.get("battle_test_provisioning"))
        if isinstance(previous_extra.get("battle_test_provisioning"), dict)
        else {}
    )
    previous_extra["battle_test_provisioning"] = {
        **battle_state,
        "status": "superseded",
        "claim_token": None,
        "updated_at": _iso(current_time),
    }
    task.extra_data = previous_extra
    task.is_active = False
    role.tasks.remove(task)

    _take_role_off_for_reconfiguration(role)
    should_dispatch = request_assessment_task_provisioning(
        role,
        reason="requisition_republish_auto_reconfigure",
        defer_until_activation=False,
        now=current_time,
    )
    intent = request_role_activation_intent(
        role,
        user_id=int(user_id),
        monthly_budget_cents=int(role.monthly_usd_budget_cents),
        auto_promote=bool(role.auto_promote),
        auto_send_assessment=role_automation_enabled(
            role, "auto_send_assessment"
        ),
        auto_resend_assessment=role_automation_enabled(
            role, "auto_resend_assessment"
        ),
        auto_advance=role_automation_enabled(role, "auto_advance"),
        now=current_time,
    )
    state = dict(role.assessment_task_provisioning or {})
    state["superseded_task_ids"] = sorted(
        {
            *[int(value) for value in (state.get("superseded_task_ids") or [])],
            previous_task_id,
        }
    )
    state["reconfiguration"] = {
        "status": "pending",
        "request_id": str(intent["request_id"]),
        "provisioning_request_id": state.get("request_id"),
        "target_role_intent_fingerprint": target_fingerprint,
        "superseded_task_id": previous_task_id,
        "requested_by_user_id": int(user_id),
        "requested_at": _iso(current_time),
        "updated_at": _iso(current_time),
        "last_error": None,
    }
    role.assessment_task_provisioning = state
    # Reconfiguration is another bootstrap cycle; keep the public status within
    # its existing starting|ready|failed contract and expose detail in the
    # durable ``reconfiguration`` payload.
    role.agent_bootstrap_status = "starting"
    role.agent_bootstrap_error = None
    role.agent_bootstrap_started_at = current_time
    role.agent_bootstrap_completed_at = None
    db.add(task)
    db.add(role)
    db.flush()
    return RequisitionReconfiguration(
        changed=True,
        status="pending",
        dispatch_generation=bool(should_dispatch),
        superseded_task_id=previous_task_id,
    )


__all__ = ["RequisitionReconfiguration", "prepare_running_role_reconfiguration"]
