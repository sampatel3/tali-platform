"""Recruiter-facing CRUD for a role's screening questions.

These questions are candidate-facing and may be knockout rules, so their
management surface participates in the same per-job authorization, optimistic
concurrency, and audit boundary as the rest of the shared job configuration.
Mounted under ``/api/v1``.
"""
from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.role_change_audit import (
    add_role_change_event,
    capture_role_change_snapshot,
    latest_role_change_actor,
)
from ...services.role_concurrency import assert_role_version, bump_role_version
from .screening_service import (
    create_role_question,
    delete_role_question,
    get_role_question,
    list_role_questions,
    update_role_question,
)

router = APIRouter(tags=["Screening questions"])


class ScreeningQuestionCreate(BaseModel):
    expected_version: int = Field(ge=1)
    prompt: str
    kind: str
    options: list | None = None
    required: bool = False
    knockout: bool = False
    knockout_expected: list | None = None
    position: int | None = None


class ScreeningQuestionUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    prompt: str | None = None
    kind: str | None = None
    options: list | None = None
    required: bool | None = None
    knockout: bool | None = None
    knockout_expected: list | None = None
    position: int | None = None
    is_active: bool | None = None


def _serialize(q, *, role_version: int | None = None) -> dict:
    # The management surface DOES include knockout config (the recruiter owns
    # it) — unlike the public payload, which strips it.
    payload = {
        "id": q.id,
        "role_id": q.role_id,
        "prompt": q.prompt,
        "kind": q.kind,
        "options": q.options,
        "required": q.required,
        "knockout": q.knockout,
        "knockout_expected": q.knockout_expected,
        "position": q.position,
        "is_active": q.is_active,
    }
    if role_version is not None:
        payload["role_version"] = int(role_version)
    return payload


def _require_mutation_role(
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
        permission=JobPermission.EDIT_ROLE,
    )
    assert_role_version(
        role,
        expected_version=expected_version,
        current_role=lambda: {
            "id": int(role.id),
            "version": int(role.version or 1),
        },
        changed_by=lambda: latest_role_change_actor(
            db,
            organization_id=int(current_user.organization_id),
            role_id=role_id,
        ),
    )
    return role


def _question_snapshot(question) -> dict:
    return {
        field: deepcopy(getattr(question, field))
        for field in (
            "prompt",
            "kind",
            "options",
            "required",
            "knockout",
            "knockout_expected",
            "position",
            "is_active",
        )
    }


def _advance_question_revision(
    db: Session,
    *,
    role: Role,
    current_user: User,
    action: str,
    reason: str,
) -> int:
    before = capture_role_change_snapshot(role)
    from_version = int(role.version or 1)
    to_version = bump_role_version(role)
    add_role_change_event(
        db,
        role=role,
        before=before,
        action=action,
        actor_user_id=int(current_user.id),
        from_version=from_version,
        to_version=to_version,
        reason=reason,
        request_id=get_request_id(),
        allow_empty_changes=True,
    )
    return to_version


@router.get("/roles/{role_id}/screening-questions")
def list_questions(
    role_id: int,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.VIEW,
    )
    rows = list_role_questions(
        db, current_user.organization_id, role_id, include_inactive=include_inactive
    )
    return [_serialize(q) for q in rows]


@router.post("/roles/{role_id}/screening-questions", status_code=201)
def create_question(
    role_id: int,
    payload: ScreeningQuestionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _require_mutation_role(
        db,
        current_user=current_user,
        role_id=role_id,
        expected_version=payload.expected_version,
    )
    q = create_role_question(
        db,
        current_user.organization_id,
        role_id,
        prompt=payload.prompt,
        kind=payload.kind,
        options=payload.options,
        required=payload.required,
        knockout=payload.knockout,
        knockout_expected=payload.knockout_expected,
        position=payload.position,
    )
    role_version = _advance_question_revision(
        db,
        role=role,
        current_user=current_user,
        action="screening_question_created",
        reason=f"candidate screening question {int(q.id)} created",
    )
    db.commit()
    db.refresh(q)
    return _serialize(q, role_version=role_version)


@router.patch("/roles/{role_id}/screening-questions/{question_id}")
def update_question(
    role_id: int,
    question_id: int,
    payload: ScreeningQuestionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _require_mutation_role(
        db,
        current_user=current_user,
        role_id=role_id,
        expected_version=payload.expected_version,
    )
    fields = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    existing = get_role_question(
        db, current_user.organization_id, role_id, question_id
    )
    before = _question_snapshot(existing)
    q = update_role_question(
        db, current_user.organization_id, role_id, question_id, fields=fields
    )
    if before != _question_snapshot(q):
        role_version = _advance_question_revision(
            db,
            role=role,
            current_user=current_user,
            action="screening_question_updated",
            reason=f"candidate screening question {int(q.id)} updated",
        )
    else:
        role_version = int(role.version or 1)
    db.commit()
    db.refresh(q)
    return _serialize(q, role_version=role_version)


@router.delete("/roles/{role_id}/screening-questions/{question_id}")
def delete_question(
    role_id: int,
    question_id: int,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = _require_mutation_role(
        db,
        current_user=current_user,
        role_id=role_id,
        expected_version=expected_version,
    )
    # 404s if the question isn't in this org+role.
    get_role_question(db, current_user.organization_id, role_id, question_id)
    delete_role_question(db, current_user.organization_id, role_id, question_id)
    role_version = _advance_question_revision(
        db,
        role=role,
        current_user=current_user,
        action="screening_question_deleted",
        reason=f"candidate screening question {int(question_id)} deleted",
    )
    db.commit()
    return {"deleted": True, "role_version": role_version}
