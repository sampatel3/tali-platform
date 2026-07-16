from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from ...deps import get_current_user
from ...models.assessment import Assessment
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import (
    RoleFeedbackNoteCreate,
    RoleFeedbackNoteResponse,
    RoleTaskLinkRequest,
)
from ...services.role_change_audit import (
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ...services.role_concurrency import assert_role_version
from .job_authorization import JobPermission, require_job_permission
from .role_management_route_support import _add_role_change_boundary
from .role_support import get_role, role_to_response

router = APIRouter(tags=["Roles"])
logger = logging.getLogger("taali.roles")


@router.get("/roles/{role_id}/tasks")
def list_role_tasks(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = (
        db.query(Role)
        .options(joinedload(Role.tasks))
        .filter(
            Role.id == role_id,
            Role.organization_id == current_user.organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    from ...services.task_battle_test import battle_test_summary

    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "scenario": t.scenario,
            "difficulty": t.difficulty,
            "duration_minutes": t.duration_minutes,
            "task_type": t.task_type,
            "is_active": bool(t.is_active),
            "generated": bool(
                isinstance(t.extra_data, dict) and t.extra_data.get("generated")
            ),
            "needs_review": bool(
                isinstance(t.extra_data, dict) and t.extra_data.get("needs_review")
            ),
            "battle_test": (
                battle_test_summary(t)
                if isinstance(t.extra_data, dict) and t.extra_data.get("generated")
                else None
            ),
        }
        for t in (role.tasks or [])
    ]


@router.post("/roles/{role_id}/tasks")
def add_role_task(
    role_id: int,
    data: RoleTaskLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    assert_role_version(role, expected_version=data.expected_version)
    task = (
        db.query(Task)
        .filter(
            Task.id == data.task_id,
            or_(
                Task.organization_id == current_user.organization_id,
                and_(
                    Task.organization_id.is_(None),
                    Task.is_template.is_(True),
                ),
            ),
        )
        .populate_existing()
        .with_for_update(of=Task)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task_extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    if not bool(task.is_active) and bool(task_extra.get("generated")):
        other_live_role = (
            db.query(Role.id)
            .join(Role.tasks)
            .filter(
                Role.id != int(role.id),
                Role.deleted_at.is_(None),
                Task.id == int(task.id),
            )
            .first()
        )
        if other_live_role is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An inactive generated draft can belong to only one live "
                    "job. Duplicate it before linking it to another job."
                ),
            )
    had_active_task = any(bool(t.is_active) for t in (role.tasks or []))
    task_was_linked = any(t.id == task.id for t in (role.tasks or []))
    first_active_task_linked = bool(
        not task_was_linked
        and bool(task.is_active)
        and not had_active_task
        and not bool(role.auto_skip_assessment)
    )
    reconcile_role_version: int | None = None
    if not task_was_linked:
        role.tasks.append(task)
        reconcile_role_version = _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action="role_task_linked",
            reason=f"assessment task {task.id} linked",
        )
    try:
        # Linking an already-active task fills the activation gap immediately;
        # an inactive generated draft intentionally leaves the prompt open
        # until the shared approval service activates it.
        db.flush()
        from ...services.agent_activation_checklist import (
            resolve_satisfied_activation_questions,
        )

        resolve_satisfied_activation_questions(db, role=role)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to link task to role")
    if first_active_task_linked:
        # The configured skip preference stays untouched. Linking the first
        # active task changes only the effective stage, so re-flow existing
        # positive cards at the authorized, versioned role boundary.
        try:
            from ...services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )

            reconcile_pending_positive_decisions(
                db,
                role_id=int(role.id),
                expected_role_version=int(reconcile_role_version),
            )
            db.commit()
        except Exception:
            logger.exception(
                "Assessment-stage reconcile failed after task link role_id=%s",
                role.id,
            )
            db.rollback()
    return {
        "success": True,
        "role_id": role.id,
        "task_id": task.id,
        "version": int(role.version or 1),
    }


@router.delete(
    "/roles/{role_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_role_task(
    role_id: int,
    task_id: int,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    assert_role_version(role, expected_version=expected_version)
    # Role is already locked by require_job_permission. Serialize on the exact
    # task before changing the association or its activation intent so task
    # approval/deactivation cannot cross this boundary concurrently.
    db.query(Task).filter(Task.id == int(task_id)).populate_existing().with_for_update(
        of=Task
    ).one_or_none()
    in_use = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == current_user.organization_id,
            Assessment.role_id == role.id,
            Assessment.task_id == task_id,
        )
        .first()
    )
    if in_use:
        raise HTTPException(
            status_code=400, detail="Cannot unlink task that already has assessments"
        )
    linked_task = next(
        (task for task in (role.tasks or []) if task.id == task_id),
        None,
    )
    had_task = linked_task is not None
    audit_before = capture_role_change_snapshot(role)
    role.tasks = [t for t in (role.tasks or []) if t.id != task_id]
    last_active_task_removed = bool(
        linked_task is not None
        and bool(linked_task.is_active)
        and not any(bool(task.is_active) for task in (role.tasks or []))
        and not bool(role.auto_skip_assessment)
    )
    reconcile_role_version: int | None = None
    if had_task:
        from ...services.role_activation_intent import (
            block_activation_intent_for_unavailable_selected_task,
        )

        block_activation_intent_for_unavailable_selected_task(
            role,
            task_id=int(task_id),
            reason=(
                "The assessment task selected for Turn on was unlinked. Select "
                "or generate another task, or skip the assessment stage, then "
                "press Turn on again."
            ),
        )
        reconcile_role_version = _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action="role_task_unlinked",
            reason=f"assessment task {task_id} unlinked",
            before=audit_before,
        )
    try:
        if last_active_task_removed and bool(role.agentic_mode_enabled):
            from ...services.agent_activation_checklist import (
                surface_activation_questions,
            )

            # The role remains configured to use assessments, so losing its
            # last executable task is an actionable runtime gap, not an
            # implicit preference rewrite.
            surface_activation_questions(db, role=role)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to unlink task from role")
    if last_active_task_removed:
        # Removing the last active task makes the stage effectively skipped,
        # but must not rewrite the recruiter's configured preference. If an
        # active task is linked later, that preference takes effect again.
        try:
            from ...services.bulk_decision_service import (
                reconcile_pending_positive_decisions,
            )

            reconcile_pending_positive_decisions(
                db,
                role_id=int(role.id),
                expected_role_version=int(reconcile_role_version),
            )
            db.commit()
        except Exception:
            logger.exception(
                "Assessment-stage reconcile failed after task unlink role_id=%s",
                role.id,
            )
            db.rollback()
    return None


# ---------------------------------------------------------------------------
# Recruiter feedback notes — freeform observations about agent behaviour on
# this role. Append-only timeline; the most-recent N rows are inlined into
# the agent's system prompt by ``system_prompt._render_recruiter_feedback_notes``
# so the agent picks the feedback up on the next cycle.
# ---------------------------------------------------------------------------


def _serialize_feedback_note(row, *, role_version: int | None = None) -> dict:
    author = row.author
    return {
        "id": int(row.id),
        "role_id": int(row.role_id),
        "author_user_id": int(row.author_user_id) if row.author_user_id else None,
        "author_name": (
            (author.full_name if getattr(author, "full_name", None) else author.email)
            if author
            else None
        ),
        "note": row.note,
        "created_at": row.created_at,
        "role_version": role_version,
    }


@router.get(
    "/roles/{role_id}/feedback-notes",
    response_model=list[RoleFeedbackNoteResponse],
)
def list_role_feedback_notes(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ...agent_runtime.role_feedback_notes import list_notes

    role = get_role(role_id, current_user.organization_id, db)
    rows = list_notes(db, role_id=role.id, limit=200)
    return [_serialize_feedback_note(r) for r in rows]


@router.post(
    "/roles/{role_id}/feedback-notes",
    response_model=RoleFeedbackNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_role_feedback_note(
    role_id: int,
    data: RoleFeedbackNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ...agent_runtime.role_feedback_notes import create_note

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    assert_role_version(
        role,
        expected_version=data.expected_version,
        current_role=lambda: role_to_response(role).model_dump(mode="json"),
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=int(role.id),
        ),
    )
    try:
        row = create_note(
            db,
            organization_id=int(current_user.organization_id),
            role_id=int(role.id),
            note=data.note,
            author_user_id=int(current_user.id),
        )
        role_version = _add_role_change_boundary(
            db,
            role=role,
            current_user=current_user,
            action="role_feedback_note_added",
            reason=f"agent feedback note {int(row.id)} added",
        )
        db.commit()
        db.refresh(row)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Failed to create role feedback note for role_id=%s", role_id)
        raise HTTPException(status_code=500, detail="Failed to create feedback note")
    return _serialize_feedback_note(row, role_version=role_version)
