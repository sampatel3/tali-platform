"""P1: role screening questions + deterministic knockout evaluation.

The application-form questions shown on the public apply form, and the
knockout gate that auto-fails an application whose answers don't meet the
required/knockout criteria — BEFORE any LLM (the cheap deterministic pre-screen).
Mutators flush but do NOT commit.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ...models.screening_question import QUESTION_KINDS, ScreeningQuestion


def list_role_questions(
    db: Session,
    organization_id: int,
    role_id: int,
    *,
    include_inactive: bool = False,
) -> list[ScreeningQuestion]:
    query = db.query(ScreeningQuestion).filter(
        ScreeningQuestion.organization_id == organization_id,
        ScreeningQuestion.role_id == role_id,
    )
    if not include_inactive:
        query = query.filter(ScreeningQuestion.is_active.is_(True))
    return query.order_by(
        ScreeningQuestion.position, ScreeningQuestion.id
    ).all()


def create_role_question(
    db: Session,
    organization_id: int,
    role_id: int,
    *,
    prompt: str,
    kind: str,
    options: list | None = None,
    required: bool = False,
    knockout: bool = False,
    knockout_expected: list | None = None,
    position: int | None = None,
) -> ScreeningQuestion:
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        raise HTTPException(status_code=422, detail="Question prompt is required")
    if kind not in QUESTION_KINDS:
        raise HTTPException(status_code=422, detail=f"Unsupported question kind={kind!r}")
    if position is None:
        current_max = (
            db.query(sa_func.max(ScreeningQuestion.position))
            .filter(
                ScreeningQuestion.organization_id == organization_id,
                ScreeningQuestion.role_id == role_id,
            )
            .scalar()
        )
        position = int(current_max) + 1 if current_max is not None else 0
    row = ScreeningQuestion(
        organization_id=organization_id,
        role_id=role_id,
        prompt=clean_prompt,
        kind=kind,
        options=options,
        required=bool(required),
        knockout=bool(knockout),
        knockout_expected=knockout_expected,
        position=position,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _is_blank(value) -> bool:
    return value is None or value == "" or value == []


def evaluate_knockouts(
    questions: list[ScreeningQuestion], answers: dict
) -> tuple[bool, list[int]]:
    """Deterministic knockout gate. Returns (passed, failed_question_ids).

    A question fails when: it is required and unanswered; or it is a knockout
    with ``knockout_expected`` set and the answer is not among the expected
    values (multi-select answers pass if any selected value is expected).
    ``answers`` is keyed by question id (str or int).
    """
    answers = answers or {}
    failed: list[int] = []
    for q in questions:
        if not q.is_active:
            continue
        ans = answers.get(str(q.id), answers.get(q.id))
        if q.required and _is_blank(ans):
            failed.append(q.id)
            continue
        if q.knockout and q.knockout_expected:
            expected = set(q.knockout_expected)
            values = set(ans) if isinstance(ans, list) else {ans}
            if not (values & expected):
                failed.append(q.id)
    return (len(failed) == 0, failed)
