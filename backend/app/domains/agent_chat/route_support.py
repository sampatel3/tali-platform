"""Request contracts and shared helpers for agent-chat HTTP routes."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...agent_chat.draft_tasks import REJECT_QUESTIONS
from ...agent_chat.service import get_owned_role
from ...models.role import Role
from ...models.user import User
from ...services.role_change_audit import latest_role_change_actor
from ...services.role_concurrency import ROLE_VERSION_CONFLICT, assert_role_version


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class BulkMessageRequest(BaseModel):
    # Explicit role ids (the recruiter's multi-selection) — no implicit
    # "all roles of type X", same deliberate choice as bulk-approve.
    role_ids: list[int] = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=8000)


class ApproveDraftRequest(BaseModel):
    expected_version: int = Field(..., ge=1)


class ReviseDraftRequest(BaseModel):
    # Structured reject answers keyed by question (e.g. {"issues": [...],
    # "direction": "harder"}) + an optional free-text note. Interpreted by
    # ``draft_tasks._build_feedback``.
    expected_version: int = Field(..., ge=1)
    answers: dict = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2000)


def require_org(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(status_code=400, detail="User has no organization")
    return int(current_user.organization_id)


def require_role(db: Session, role_id: int, organization_id: int) -> Role:
    role = get_owned_role(db, role_id=role_id, organization_id=organization_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def agent_meta(role: Role) -> dict:
    return {
        "version": int(role.version or 1),
        "enabled": bool(role.agentic_mode_enabled),
        "paused": role.agent_paused_at is not None,
        "paused_reason": role.agent_paused_reason,
        "monthly_budget_cents": role.monthly_usd_budget_cents,
        "score_threshold": role.score_threshold,
    }


def draft_current_role(role: Role) -> dict:
    return {
        "id": int(role.id),
        "name": role.name,
        "version": int(role.version or 1),
        "agent": agent_meta(role),
    }


def assert_draft_role_version(
    db: Session,
    role: Role,
    expected_version: int,
) -> None:
    assert_role_version(
        role,
        expected_version=expected_version,
        current_role=lambda: draft_current_role(role),
        changed_by=lambda: latest_role_change_actor(
            db,
            int(role.organization_id),
            int(role.id),
        ),
    )


def draft_conflict(db: Session, role: Role) -> HTTPException:
    """Return the standard conflict for task-only drift at the same Role rev."""
    return HTTPException(
        status_code=409,
        detail={
            "code": ROLE_VERSION_CONFLICT,
            "message": (
                "This job changed after you opened it. Review the latest "
                "version before saving your changes."
            ),
            "current_version": int(role.version or 1),
            "current_role": draft_current_role(role),
            "changed_by": latest_role_change_actor(
                db,
                int(role.organization_id),
                int(role.id),
            ),
        },
    )


def draft_review_card(role: Role, summary: dict) -> dict:
    """Build a review card focused on one newly revised draft."""
    return {
        "type": "draft_task_review",
        "role_id": int(role.id),
        "role_version": int(role.version or 1),
        "drafts": [summary],
        "reject_questions": REJECT_QUESTIONS,
    }
