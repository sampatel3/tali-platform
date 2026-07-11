"""Role screening questions + deterministic knockout evaluation.

The application-form questions shown on the public apply form, and the knockout
gate that auto-fails an application whose answers don't meet the required /
knockout criteria — BEFORE any LLM (the cheap deterministic pre-screen).

Mutators flush but do NOT commit; the caller (route) owns the transaction.
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


def get_role_question(
    db: Session, organization_id: int, role_id: int, question_id: int
) -> ScreeningQuestion:
    """Fetch one org+role-scoped question or raise 404. Keeps every mutator
    org-scoped so one org can never touch another's questions."""
    row = (
        db.query(ScreeningQuestion)
        .filter(
            ScreeningQuestion.id == question_id,
            ScreeningQuestion.organization_id == organization_id,
            ScreeningQuestion.role_id == role_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Screening question not found")
    return row


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
        raise HTTPException(
            status_code=422, detail=f"Unsupported question kind={kind!r}"
        )
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


def update_role_question(
    db: Session,
    organization_id: int,
    role_id: int,
    question_id: int,
    *,
    fields: dict,
) -> ScreeningQuestion:
    """Patch the mutable attributes of a question. Only keys present in
    ``fields`` are touched. ``prompt`` and ``kind`` are re-validated."""
    row = get_role_question(db, organization_id, role_id, question_id)
    if "prompt" in fields:
        clean_prompt = (fields["prompt"] or "").strip()
        if not clean_prompt:
            raise HTTPException(
                status_code=422, detail="Question prompt is required"
            )
        row.prompt = clean_prompt
    if "kind" in fields:
        if fields["kind"] not in QUESTION_KINDS:
            raise HTTPException(
                status_code=422, detail=f"Unsupported question kind={fields['kind']!r}"
            )
        row.kind = fields["kind"]
    for attr in ("options", "knockout_expected"):
        if attr in fields:
            setattr(row, attr, fields[attr])
    for attr in ("required", "knockout", "is_active"):
        if attr in fields:
            setattr(row, attr, bool(fields[attr]))
    if "position" in fields and fields["position"] is not None:
        row.position = int(fields["position"])
    db.flush()
    return row


def delete_role_question(
    db: Session, organization_id: int, role_id: int, question_id: int
) -> None:
    """Hard-delete a question (there is no soft-delete on questions; deactivate
    via ``is_active`` if a question must be retired without losing history)."""
    row = get_role_question(db, organization_id, role_id, question_id)
    db.delete(row)
    db.flush()


# --------------------------------------------------------------------------- #
# Deterministic knockout gate
# --------------------------------------------------------------------------- #

# Scalar answer values the gate can reason about. Anything else (a dict, or a
# list containing a dict/list) is malformed and rejected with a 422 BEFORE any
# set operation, so a bad payload never raises a TypeError 500 deep in the gate.
_SCALAR_TYPES = (str, int, float, bool)


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, _SCALAR_TYPES)


def _validate_answer_shape(question_id: int, ans) -> None:
    """Raise HTTP 422 for a malformed answer value. Valid shapes: a scalar
    (str / number / bool / None) or a flat list of scalars. A dict answer, or a
    list containing a dict / nested list, is rejected cleanly."""
    if isinstance(ans, dict):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid answer format for question {question_id}",
        )
    if isinstance(ans, list):
        if not all(_is_scalar(item) for item in ans):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid answer format for question {question_id}",
            )
        return
    if not _is_scalar(ans):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid answer format for question {question_id}",
        )


def _is_blank(value) -> bool:
    return value is None or value == "" or value == []


def evaluate_knockouts(
    questions: list[ScreeningQuestion], answers: dict | None
) -> tuple[bool, list[int]]:
    """Deterministic knockout gate. Returns (passed, failed_question_ids).

    A question fails when: it is required and unanswered; or it is a knockout
    with ``knockout_expected`` set and the answer is not among the expected
    values (multi-select answers pass if any selected value is expected).
    ``answers`` is keyed by question id (str or int).

    Malformed answer values (a dict, or a list containing a dict / nested list)
    raise HTTP 422 rather than crashing the set-intersection with a TypeError.
    A non-dict ``answers`` payload is also a 422.
    """
    if answers is None:
        answers = {}
    if not isinstance(answers, dict):
        raise HTTPException(status_code=422, detail="Invalid answers payload")
    failed: list[int] = []
    for q in questions:
        if not q.is_active:
            continue
        ans = answers.get(str(q.id), answers.get(q.id))
        _validate_answer_shape(q.id, ans)
        if q.required and _is_blank(ans):
            failed.append(q.id)
            continue
        if q.knockout and q.knockout_expected:
            expected = set(q.knockout_expected)
            values = set(ans) if isinstance(ans, list) else {ans}
            if not (values & expected):
                failed.append(q.id)
    return (len(failed) == 0, failed)
