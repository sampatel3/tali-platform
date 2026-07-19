"""Two-phase completion of a durable role Turn-on command."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from .role_activation_intent import refresh_role_activation_intent_policy
from .role_activation_task_completion import (
    ActivationTaskPreparation,
    apply_activation_task_preparation,
    capture_activation_task_for_completion,
)
from .role_activation_task_guard import (
    ACTIVATION_ACTIVE_STATUSES,
    ACTIVATION_SUCCEEDED,
    activation_intent_is_due,
    activation_intent_state,
    iso_time,
    lock_activation_role,
    record_activation_retry,
    utcnow,
    write_activation_intent,
)
from .task_approval_service import TaskApprovalError

logger = logging.getLogger("taali.role_activation_intent")


def _locked_activation_guard(
    db: Session,
    *,
    role: Role,
    request_id: str,
    worker_task_id: str | None,
    now: datetime,
) -> tuple[dict[str, Any], int, bool] | dict[str, Any]:
    intent = activation_intent_state(role)
    if str(intent.get("request_id") or "") != str(request_id or ""):
        return {"status": "superseded"}
    status = str(intent.get("status") or "")
    if status == ACTIVATION_SUCCEEDED:
        same_worker = bool(
            worker_task_id
            and str(intent.get("activation_worker_task_id") or "")
            == str(worker_task_id)
        )
        return {
            "status": "already_activated" if same_worker else "duplicate",
            "role_id": int(role.id),
        }
    if status not in ACTIVATION_ACTIVE_STATUSES:
        return {"status": status or "inactive"}
    if not activation_intent_is_due(role, now=now):
        return {"status": "not_due"}
    if bool(role.agentic_mode_enabled):
        return record_activation_retry(
            db,
            role_id=int(role.id),
            request_id=request_id,
            error="role was enabled by a different activation command",
            now=now,
            blocked=True,
        )
    current_budget = getattr(role, "monthly_usd_budget_cents", None)
    if current_budget is None or int(current_budget) <= 0:
        return record_activation_retry(
            db,
            role_id=int(role.id),
            request_id=request_id,
            error=(
                "monthly_usd_budget_cents must be greater than zero before "
                "the agent can turn on"
            ),
            now=now,
            blocked=True,
        )
    return intent, int(current_budget), bool(role.auto_skip_assessment)


def _task_approval_retry(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    error: TaskApprovalError,
    now: datetime,
) -> dict[str, Any]:
    retryable_codes = {"task_repository_unavailable", "task_approval_superseded"}
    return record_activation_retry(
        db,
        role_id=role_id,
        request_id=request_id,
        error=error.public_detail,
        now=now,
        blocked=error.code not in retryable_codes,
    )


def _unlocked_readiness(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    preparation: ActivationTaskPreparation,
    now: datetime,
) -> tuple[int, int] | dict[str, Any]:
    """Run every external readiness probe without holding row locks."""

    role = (
        db.query(Role)
        .filter(Role.id == role_id, Role.deleted_at.is_(None))
        .populate_existing()
        .one_or_none()
    )
    if role is None:
        db.rollback()
        return {"status": "missing"}
    intent = activation_intent_state(role)
    if str(intent.get("request_id") or "") != str(request_id):
        db.rollback()
        return {"status": "superseded"}
    if str(intent.get("status") or "") not in ACTIVATION_ACTIVE_STATUSES:
        status = str(intent.get("status") or "inactive")
        db.rollback()
        return {"status": status}
    budget = getattr(role, "monthly_usd_budget_cents", None)
    if budget is None or int(budget) <= 0:
        return record_activation_retry(
            db,
            role_id=role_id,
            request_id=request_id,
            error="monthly_usd_budget_cents must be greater than zero",
            now=now,
            blocked=True,
        )

    from .agent_activation_readiness import activation_readiness, readiness_message
    from .agent_policy_settings import role_automation_enabled

    preview_task_id = (
        preparation.task_id
        if preparation.captured_approval is not None
        else None
    )
    readiness = activation_readiness(
        role,
        auto_skip_assessment=bool(role.auto_skip_assessment),
        monthly_usd_budget_cents=int(budget),
        auto_send_assessment=role_automation_enabled(role, "auto_send_assessment"),
        auto_resend_assessment=role_automation_enabled(
            role, "auto_resend_assessment"
        ),
        auto_advance=role_automation_enabled(role, "auto_advance"),
        preview_active_task_id=preview_task_id,
    )
    if not readiness.get("ready"):
        return record_activation_retry(
            db,
            role_id=role_id,
            request_id=request_id,
            error=readiness_message(readiness),
            now=now,
        )
    return int(getattr(role, "version", 1) or 1), int(
        intent.get("policy_revision") or 0
    )


def _activate_locked_role(
    db: Session,
    *,
    role: Role,
    intent: dict[str, Any],
    task_id: int | None,
    request_id: str,
    worker_task_id: str | None,
    now: datetime,
) -> None:
    from .role_change_audit import (
        ROLE_CHANGE_ACTION_AGENT_ENABLED,
        add_role_change_event,
        capture_role_change_snapshot,
    )

    audit_before = capture_role_change_snapshot(role)
    audit_from_version = int(getattr(role, "version", 1) or 1)
    role.agentic_mode_enabled = True
    role.agent_paused_at = None
    role.agent_paused_reason = None
    role.starred_for_auto_sync = True
    if role.source == "requisition" and role.job_status == JOB_STATUS_DRAFT:
        role.job_status = JOB_STATUS_OPEN
    role.agent_bootstrap_status = "starting"
    role.agent_bootstrap_error = None
    role.agent_bootstrap_started_at = now
    role.agent_bootstrap_completed_at = None
    refresh_role_activation_intent_policy(role, now=now)
    intent = activation_intent_state(role)
    intent.update(
        {
            "status": ACTIVATION_SUCCEEDED,
            "task_id": task_id,
            "attempts": int(intent.get("attempts") or 0) + 1,
            "last_error": None,
            "next_attempt_at": None,
            "activation_worker_task_id": worker_task_id,
            "activated_at": iso_time(now),
            "completed_at": iso_time(now),
            "updated_at": iso_time(now),
        }
    )
    write_activation_intent(role, intent)
    provisioning = dict(role.assessment_task_provisioning or {})
    reconfiguration = provisioning.get("reconfiguration")
    if isinstance(reconfiguration, dict) and str(
        reconfiguration.get("status") or ""
    ) in {"pending", "running"}:
        provisioning["reconfiguration"] = {
            **reconfiguration,
            "status": "succeeded",
            "replacement_task_id": task_id,
            "last_error": None,
            "completed_at": iso_time(now),
            "updated_at": iso_time(now),
        }
    provisioning["interview_focus_provisioning"] = {
        "status": "succeeded" if bool(role.interview_focus) else "pending",
        "last_error": None,
        "next_attempt_at": None,
        "updated_at": iso_time(now),
    }
    provisioning["tech_questions_provisioning"] = {
        "status": "succeeded" if bool(role.tech_questions_signature) else "pending",
        "last_error": None,
        "next_attempt_at": None,
        "updated_at": iso_time(now),
    }
    role.assessment_task_provisioning = provisioning

    from ..models.user import User
    from .role_concurrency import bump_role_version

    audit_to_version = bump_role_version(role)
    requested_actor_id = (
        int(intent["requested_by_user_id"])
        if intent.get("requested_by_user_id") is not None
        else None
    )
    actor_user_id = None
    if requested_actor_id is not None:
        actor_user_id = (
            db.query(User.id)
            .filter(
                User.id == requested_actor_id,
                User.organization_id == int(role.organization_id),
            )
            .scalar()
        )
    add_role_change_event(
        db,
        role=role,
        before=audit_before,
        action=ROLE_CHANGE_ACTION_AGENT_ENABLED,
        actor_user_id=int(actor_user_id) if actor_user_id is not None else None,
        from_version=audit_from_version,
        to_version=audit_to_version,
        reason="deferred activation completed after assessment provisioning",
        request_id=str(request_id),
    )
    db.add(role)


def _complete_role_activation_intent_serialized(
    db: Session,
    *,
    role_id: int,
    request_id: str,
    worker_task_id: str | None = None,
    now: datetime | None = None,
    repository_mutex_task_id: int | None = None,
) -> dict[str, Any]:
    """Prepare remotely without locks, then atomically revalidate and turn on."""

    current_time = now or utcnow()
    role, workspace_paused = lock_activation_role(
        db, role_id=int(role_id), fail_if_workspace_paused=True
    )
    if workspace_paused:
        return {"status": "workspace_paused"}
    if role is None:
        return {"status": "missing"}
    guarded = _locked_activation_guard(
        db,
        role=role,
        request_id=request_id,
        worker_task_id=worker_task_id,
        now=current_time,
    )
    if isinstance(guarded, dict):
        db.rollback()
        return guarded
    intent, _budget, skip_assessment = guarded
    preparation, task_result = capture_activation_task_for_completion(
        db,
        role=role,
        intent=intent,
        request_id=request_id,
        now=current_time,
        skip_assessment=skip_assessment,
    )
    if task_result is not None:
        db.rollback()
        return task_result
    assert preparation is not None
    if (
        preparation.captured_approval is not None
        and preparation.task_id != repository_mutex_task_id
    ):
        return record_activation_retry(
            db,
            role_id=int(role_id),
            request_id=request_id,
            error="activation task selection changed before repository preparation",
            now=current_time,
        )

    db.rollback()
    prepared_approval = None
    if preparation.captured_approval is not None:
        from .task_approval_service import prepare_task_approval

        try:
            prepared_approval = prepare_task_approval(
                preparation.captured_approval
            )
        except TaskApprovalError as exc:
            return _task_approval_retry(
                db,
                role_id=int(role_id),
                request_id=request_id,
                error=exc,
                now=current_time,
            )
        except Exception:
            logger.exception(
                "role activation approval preparation failed role_id=%s request_id=%s",
                role_id,
                request_id,
            )
            return record_activation_retry(
                db,
                role_id=int(role_id),
                request_id=request_id,
                error="activation_failed",
                now=current_time,
            )

    try:
        readiness_state = _unlocked_readiness(
            db,
            role_id=int(role_id),
            request_id=request_id,
            preparation=preparation,
            now=current_time,
        )
        if isinstance(readiness_state, dict):
            return readiness_state
        readiness_version, readiness_policy_revision = readiness_state
        db.rollback()

        role, workspace_paused = lock_activation_role(
            db, role_id=int(role_id), fail_if_workspace_paused=True
        )
        if workspace_paused:
            return {"status": "workspace_paused"}
        if role is None:
            return {"status": "missing"}
        guarded = _locked_activation_guard(
            db,
            role=role,
            request_id=request_id,
            worker_task_id=worker_task_id,
            now=current_time,
        )
        if isinstance(guarded, dict):
            db.rollback()
            return guarded
        intent, _budget, _skip_assessment = guarded
        if (
            int(getattr(role, "version", 1) or 1) != readiness_version
            or int(intent.get("policy_revision") or 0)
            != readiness_policy_revision
        ):
            return record_activation_retry(
                db,
                role_id=int(role_id),
                request_id=request_id,
                error="role activation settings changed during readiness",
                now=current_time,
            )
        task = apply_activation_task_preparation(
            db,
            role=role,
            intent=intent,
            preparation=preparation,
            prepared_approval=prepared_approval,
        )
        _activate_locked_role(
            db,
            role=role,
            intent=intent,
            task_id=int(task.id) if task is not None else None,
            request_id=request_id,
            worker_task_id=worker_task_id,
            now=current_time,
        )
        db.commit()
    except TaskApprovalError as exc:
        return _task_approval_retry(
            db,
            role_id=int(role_id),
            request_id=request_id,
            error=exc,
            now=current_time,
        )
    except Exception:
        logger.exception(
            "role activation failed role_id=%s request_id=%s",
            role_id,
            request_id,
        )
        return record_activation_retry(
            db,
            role_id=int(role_id),
            request_id=request_id,
            error="activation_failed",
            now=current_time,
        )

    try:
        from .agent_activation_checklist import surface_activation_questions

        surface_activation_questions(db, role=role)
        db.commit()
    except Exception:
        logger.exception("activation checklist failed role_id=%s", role.id)
        db.rollback()
    try:
        from .application_events import on_role_jd_attached

        on_role_jd_attached(role)
        from ..tasks.automation_tasks import regenerate_role_tech_questions

        regenerate_role_tech_questions.delay(int(role.id))
    except Exception:
        logger.exception("activation artifact dispatch failed role_id=%s", role.id)
    return {
        "status": "activated",
        "role_id": int(role.id),
        "task_id": preparation.task_id,
    }


__all__ = ["_complete_role_activation_intent_serialized"]
