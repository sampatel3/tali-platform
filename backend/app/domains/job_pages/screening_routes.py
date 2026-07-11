"""Recruiter-facing CRUD for a role's screening questions.

Org-scoped, authenticated (plain current user). The public apply form and the
knockout gate read these; this is the management surface the ats branch never
built. Mounted under ``/api/v1``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db
from .screening_service import (
    create_role_question,
    delete_role_question,
    get_role_question,
    list_role_questions,
    update_role_question,
)

router = APIRouter(tags=["Screening questions"])


class ScreeningQuestionCreate(BaseModel):
    prompt: str
    kind: str
    options: list | None = None
    required: bool = False
    knockout: bool = False
    knockout_expected: list | None = None
    position: int | None = None


class ScreeningQuestionUpdate(BaseModel):
    prompt: str | None = None
    kind: str | None = None
    options: list | None = None
    required: bool | None = None
    knockout: bool | None = None
    knockout_expected: list | None = None
    position: int | None = None
    is_active: bool | None = None


def _serialize(q) -> dict:
    # The management surface DOES include knockout config (the recruiter owns
    # it) — unlike the public payload, which strips it.
    return {
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


def _require_role(db: Session, org_id: int, role_id: int) -> Role:
    role = (
        db.query(Role)
        .filter(Role.id == role_id, Role.organization_id == org_id)
        .first()
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.get("/roles/{role_id}/screening-questions")
def list_questions(
    role_id: int,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(db, current_user.organization_id, role_id)
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
    _require_role(db, current_user.organization_id, role_id)
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
    db.commit()
    db.refresh(q)
    return _serialize(q)


@router.patch("/roles/{role_id}/screening-questions/{question_id}")
def update_question(
    role_id: int,
    question_id: int,
    payload: ScreeningQuestionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(db, current_user.organization_id, role_id)
    fields = payload.model_dump(exclude_unset=True)
    q = update_role_question(
        db, current_user.organization_id, role_id, question_id, fields=fields
    )
    db.commit()
    db.refresh(q)
    return _serialize(q)


@router.delete("/roles/{role_id}/screening-questions/{question_id}", status_code=204)
def delete_question(
    role_id: int,
    question_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_role(db, current_user.organization_id, role_id)
    # 404s if the question isn't in this org+role.
    get_role_question(db, current_user.organization_id, role_id, question_id)
    delete_role_question(db, current_user.organization_id, role_id, question_id)
    db.commit()
