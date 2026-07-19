"""Short-lock orchestration for approving a generated chat draft."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...agent_chat.draft_tasks import (
    apply_prepared_draft_approval,
    capture_draft_approval,
)
from ...agent_chat.service import (
    ensure_conversation,
    post_agent_message,
)
from ...agent_chat.timeline import build_timeline
from ...models.user import User
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
)
from ...services.role_concurrency import bump_role_version
from ...services.task_approval_service import (
    TaskApprovalError,
    prepare_task_approval,
)
from ...services.task_repository_serialization import (
    task_repository_write_mutex,
)
from ..assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from .route_support import assert_draft_role_version


def approve_draft_task_command(
    db: Session,
    *,
    current_user: User,
    organization_id: int,
    role_id: int,
    task_id: int,
    expected_version: int,
) -> dict:
    """Prepare outside locks, then reauthorize and apply exact task content."""

    with task_repository_write_mutex(db, task_id=task_id):
        return _approve_draft_task_command(
            db,
            current_user=current_user,
            organization_id=organization_id,
            role_id=role_id,
            task_id=task_id,
            expected_version=expected_version,
        )


def _approve_draft_task_command(
    db: Session,
    *,
    current_user: User,
    organization_id: int,
    role_id: int,
    task_id: int,
    expected_version: int,
) -> dict:
    """Execute while the caller owns this task's repository writer mutex."""

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    db.refresh(role)
    assert_draft_role_version(db, role, expected_version)
    actor_user_id = int(current_user.id)
    captured = capture_draft_approval(db, role, task_id)
    if not captured.get("ok"):
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=captured.get("error") or "Approve failed",
        )

    # Provider/filesystem work uses a detached snapshot, never a locked ORM
    # row. The second phase rejects any intervening authorization/content edit.
    db.rollback()
    try:
        prepared = prepare_task_approval(captured["captured"])
    except TaskApprovalError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=exc.public_detail) from exc

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    db.refresh(role)
    assert_draft_role_version(db, role, expected_version)
    from_version = int(role.version or 1)
    before = capture_role_change_snapshot(role)
    try:
        result = apply_prepared_draft_approval(
            db,
            role,
            task_id,
            prepared,
            user_id=actor_user_id,
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error") or "Approve failed",
            )
        summary = result["summary"]
        to_version = bump_role_version(role)
        add_role_change_event(
            db,
            role=role,
            before=before,
            action="role_draft_task_approved",
            actor_user_id=actor_user_id,
            from_version=from_version,
            to_version=to_version,
            reason=f"Draft task {int(task_id)} approved from agent chat",
            allow_empty_changes=True,
        )
        conversation = ensure_conversation(
            db,
            organization_id=organization_id,
            role=role,
        )
        post_agent_message(
            db,
            conversation=conversation,
            text=f"Approved **{summary['name']}** — it's live and assignable now.",
        )
        timeline = build_timeline(db, conversation=conversation, role=role)
        db.commit()
    except TaskApprovalError as exc:
        db.rollback()
        raise HTTPException(
            status_code=(409 if exc.code == "task_approval_superseded" else 400),
            detail=exc.public_detail,
        ) from exc
    except Exception:
        db.rollback()
        raise
    return {
        "ok": True,
        "role_id": role.id,
        "role_version": to_version,
        "summary": summary,
        "timeline": timeline,
    }


__all__ = ["approve_draft_task_command"]
