"""Role-scoped recruiter-input commands for Agent Chat.

The HTTP needs-input surface and the autonomous runtime already share the
``ask_recruiter`` action.  Agent Chat should use that same action rather than
reimplementing resolution, auditing, or canonical role write-back.  This
module adds the pieces that a conversational command needs around it:

* strict organization *and role* scoping (a guessed id from another role is
  deliberately indistinguishable from a missing id),
* a compact representation of the role's open questions,
* validation and normalization for option, free-text, and numeric answers,
* an explicit dismissal permission bit carried in ``response_schema``.

No function commits.  The Agent Chat turn owns the transaction so the answer
and its transcript are persisted atomically.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..actions import ask_recruiter
from ..actions.types import Actor
from ..models.agent_needs_input import AgentNeedsInput
from ..models.role import Role
from ..models.user import User


MAX_LIST_LIMIT = 50

_INTEGER_INPUT_KINDS = frozenset({"threshold_ambiguous"})
_NUMBER_INPUT_KINDS = frozenset({"monthly_budget_missing"})


# Kept next to the implementation so tools.py can import and append the
# definitions without duplicating the public contract.
RECRUITER_INPUT_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_open_recruiter_inputs",
        "description": (
            "List unanswered questions the agent has asked about THIS role. "
            "Returns each question's id, response mode, allowed options and "
            "whether it may be dismissed. Call this before answering or "
            "dismissing so you use a live id and the current response contract."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIST_LIMIT,
                    "default": 20,
                }
            },
            "required": [],
        },
    },
    {
        "name": "answer_recruiter_input",
        "description": (
            "Answer one open agent question for THIS role. Use an option value "
            "or label for choice questions, plain text for text questions, and "
            "a number for numeric questions. The server validates the live "
            "question before recording the recruiter answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "needs_input_id": {"type": "integer"},
                "value": {
                    "type": ["string", "number", "boolean"],
                    "description": "The recruiter's answer; never invent one.",
                },
            },
            "required": ["needs_input_id", "value"],
        },
    },
    {
        "name": "dismiss_recruiter_input",
        "description": (
            "Dismiss one open agent question for THIS role without answering. "
            "Only succeeds when that question's live response contract permits "
            "dismissal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"needs_input_id": {"type": "integer"}},
            "required": ["needs_input_id"],
        },
    },
]


def list_open_recruiter_inputs(
    db: Session,
    *,
    role: Role,
    limit: int = 20,
) -> dict[str, Any]:
    """Return the newest open needs-input requests for ``role`` only."""

    try:
        bounded_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="limit must be an integer") from exc
    if not 1 <= bounded_limit <= MAX_LIST_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {MAX_LIST_LIMIT}",
        )

    rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.organization_id == int(role.organization_id),
            AgentNeedsInput.role_id == int(role.id),
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .order_by(AgentNeedsInput.created_at.desc(), AgentNeedsInput.id.desc())
        .limit(bounded_limit)
        .all()
    )
    requests = [_serialize_open_request(row) for row in rows]
    return {
        "type": "recruiter_input_queue",
        "role_id": int(role.id),
        "open_count": len(requests),
        "requests": requests,
    }


def answer_recruiter_input(
    db: Session,
    *,
    role: Role,
    user: User,
    needs_input_id: int,
    value: Any,
    expected_role_version: int,
) -> dict[str, Any]:
    """Validate and answer one open request on this conversation's role.

    Persistence and kind-specific write-back (threshold, budget, role intent,
    and material-change confirmation) are delegated to
    :func:`app.actions.ask_recruiter.answer`.
    """

    _assert_user_can_act_on_role(user, role)
    row = _get_open_role_request(
        db,
        role=role,
        needs_input_id=needs_input_id,
    )
    response = _validated_response(row, value)
    answered = ask_recruiter.answer(
        db,
        Actor.recruiter(user),
        organization_id=int(role.organization_id),
        needs_input_id=int(row.id),
        response=response,
        expected_version=int(expected_role_version),
    )
    return {
        "type": "recruiter_input_answered",
        "role_id": int(role.id),
        "needs_input_id": int(answered.id),
        "question_kind": answered.kind,
        "status": "answered",
        "response": answered.response,
    }


def dismiss_recruiter_input(
    db: Session,
    *,
    role: Role,
    user: User,
    needs_input_id: int,
) -> dict[str, Any]:
    """Dismiss an open request when its response contract permits it."""

    _assert_user_can_act_on_role(user, role)
    row = _get_open_role_request(
        db,
        role=role,
        needs_input_id=needs_input_id,
    )
    if not recruiter_input_allows_dismiss(row):
        raise HTTPException(
            status_code=403,
            detail="this recruiter question must be answered and cannot be dismissed",
        )
    dismissed = ask_recruiter.dismiss(
        db,
        Actor.recruiter(user),
        organization_id=int(role.organization_id),
        needs_input_id=int(row.id),
    )
    return {
        "type": "recruiter_input_dismissed",
        "role_id": int(role.id),
        "needs_input_id": int(dismissed.id),
        "question_kind": dismissed.kind,
        "status": "dismissed",
    }


def _assert_user_can_act_on_role(user: User, role: Role) -> None:
    if int(user.organization_id) != int(role.organization_id):
        raise HTTPException(status_code=404, detail="role not found")


def _get_open_role_request(
    db: Session,
    *,
    role: Role,
    needs_input_id: int,
) -> AgentNeedsInput:
    try:
        request_id = int(needs_input_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="needs_input_id must be an integer") from exc
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.id == request_id,
            AgentNeedsInput.organization_id == int(role.organization_id),
            AgentNeedsInput.role_id == int(role.id),
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .one_or_none()
    )
    if row is None:
        # A single 404 covers absent, closed, cross-org, and cross-role ids so
        # the role-scoped chat cannot probe another role's queue.
        raise HTTPException(
            status_code=404,
            detail="open recruiter question not found for this role",
        )
    return row


def _serialize_open_request(row: AgentNeedsInput) -> dict[str, Any]:
    schema = _schema(row)
    options = _options(row)
    contract = recruiter_input_contract(row)
    return {
        "needs_input_id": int(row.id),
        "question_kind": row.kind,
        "prompt": row.prompt,
        "rationale": row.rationale,
        "input_mode": contract["input_mode"],
        "options": options or None,
        "response_schema": schema or None,
        "can_answer": contract["can_answer"],
        "can_dismiss": contract["can_dismiss"],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def recruiter_input_contract(row: AgentNeedsInput) -> dict[str, Any]:
    """Project the live answer controls shared by list and timeline surfaces."""

    schema = _schema(row)
    return {
        "input_mode": _input_mode(row, schema=schema, options=_options(row)),
        "can_answer": not ask_recruiter.requires_external_resolution(row.kind),
        "can_dismiss": recruiter_input_allows_dismiss(row),
    }


def _validated_response(row: AgentNeedsInput, raw_value: Any) -> dict[str, Any]:
    if ask_recruiter.requires_external_resolution(row.kind):
        raise HTTPException(
            status_code=422,
            detail=ask_recruiter.EXTERNAL_RESOLUTION_DETAIL,
        )

    options = _options(row)
    schema = _value_schema_for_row(row)

    if options:
        option = _match_option(options, raw_value)
        if option is not None:
            response: dict[str, Any] = {"value": option["value"]}
            if option.get("label") is not None:
                response["label"] = option["label"]
            return response
        # Threshold cards intentionally show one recommended option while the
        # canonical prompt allows a recruiter to type a different number.
        if row.kind != "threshold_ambiguous":
            allowed = ", ".join(str(o.get("label") or o["value"]) for o in options)
            raise HTTPException(
                status_code=422,
                detail=f"answer must be one of the offered options: {allowed}",
            )

    normalized = _normalize_value(row, schema=schema, raw_value=raw_value)
    return {"value": normalized}


def _options(row: AgentNeedsInput) -> list[dict[str, Any]]:
    if not isinstance(row.options, list):
        return []
    return [
        {"value": item["value"], "label": item.get("label")}
        for item in row.options
        if isinstance(item, Mapping) and "value" in item
    ]


def _match_option(
    options: list[dict[str, Any]], raw_value: Any
) -> dict[str, Any] | None:
    # Preserve option value types in the stored response. Exact equality gets
    # priority; case-insensitive matching of labels and string values makes
    # natural-language chat answers ergonomic without weakening the enum.
    for option in options:
        if raw_value == option["value"] and type(raw_value) is type(option["value"]):
            return option
    if isinstance(raw_value, str):
        needle = raw_value.strip().casefold()
        for option in options:
            candidates = (option.get("value"), option.get("label"))
            if any(
                isinstance(candidate, str) and candidate.strip().casefold() == needle
                for candidate in candidates
            ):
                return option
    return None


def _schema(row: AgentNeedsInput) -> dict[str, Any]:
    return dict(row.response_schema) if isinstance(row.response_schema, Mapping) else {}


def _value_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Accept either a scalar descriptor or JSON Schema's ``properties.value``."""

    properties = schema.get("properties")
    if isinstance(properties, Mapping) and isinstance(properties.get("value"), Mapping):
        return dict(properties["value"])
    return dict(schema)


def _value_schema_for_row(row: AgentNeedsInput) -> dict[str, Any]:
    """Return the stored descriptor plus invariants of canonical question kinds."""

    schema = _value_schema(_schema(row))
    if row.kind == "threshold_ambiguous":
        schema.setdefault("type", "integer")
        schema.setdefault("minimum", 0)
        schema.setdefault("maximum", 100)
    elif row.kind == "monthly_budget_missing":
        schema.setdefault("type", "number")
        schema.setdefault("minimum", 0)
    return schema


def _input_mode(
    row: AgentNeedsInput,
    *,
    schema: Mapping[str, Any],
    options: list[dict[str, Any]],
) -> str:
    if ask_recruiter.requires_external_resolution(row.kind):
        return "external"
    if options:
        return "option_or_number" if row.kind == "threshold_ambiguous" else "option"
    return _expected_type(row, _value_schema_for_row(row))


def _expected_type(row: AgentNeedsInput, schema: Mapping[str, Any]) -> str:
    raw_type: Any = schema.get("type") or schema.get("input_type")
    if isinstance(raw_type, list):
        raw_type = next((item for item in raw_type if item != "null"), None)
    aliases = {
        "text": "string",
        "textarea": "string",
        "numeric": "number",
        "float": "number",
        "int": "integer",
    }
    if isinstance(raw_type, str):
        normalized = aliases.get(raw_type.strip().lower(), raw_type.strip().lower())
        if normalized in {"string", "number", "integer", "boolean"}:
            return normalized
    if row.kind in _INTEGER_INPUT_KINDS:
        return "integer"
    if row.kind in _NUMBER_INPUT_KINDS:
        return "number"
    return "string"


def _normalize_value(
    row: AgentNeedsInput,
    *,
    schema: Mapping[str, Any],
    raw_value: Any,
) -> Any:
    expected = _expected_type(row, schema)
    if expected == "string":
        return _validate_string(schema, raw_value)
    if expected in {"number", "integer"}:
        return _validate_number(schema, raw_value, integer=expected == "integer")
    if expected == "boolean":
        return _validate_boolean(schema, raw_value)
    raise HTTPException(status_code=422, detail=f"unsupported response type: {expected}")


def _validate_string(schema: Mapping[str, Any], raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        raise HTTPException(status_code=422, detail="answer must be text")
    value = raw_value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="answer cannot be empty")

    minimum = schema.get("minLength", schema.get("min_length"))
    maximum = schema.get("maxLength", schema.get("max_length"))
    if minimum is not None and len(value) < int(minimum):
        raise HTTPException(
            status_code=422,
            detail=f"answer must be at least {int(minimum)} characters",
        )
    if maximum is not None and len(value) > int(maximum):
        raise HTTPException(
            status_code=422,
            detail=f"answer must be at most {int(maximum)} characters",
        )

    pattern = schema.get("pattern")
    if pattern:
        try:
            matched = re.search(str(pattern), value) is not None
        except re.error as exc:
            raise HTTPException(
                status_code=422,
                detail="question has an invalid text-validation pattern",
            ) from exc
        if not matched:
            raise HTTPException(status_code=422, detail="answer does not match the required format")
    _validate_enum(schema, value)
    return value


def _decimal(raw_value: Any) -> Decimal:
    if isinstance(raw_value, bool):
        raise HTTPException(status_code=422, detail="answer must be a number")
    if isinstance(raw_value, str):
        cleaned = raw_value.strip().replace(",", "")
        if cleaned.startswith("$"):
            cleaned = cleaned[1:].strip()
    elif isinstance(raw_value, (int, float, Decimal)):
        if isinstance(raw_value, float) and not math.isfinite(raw_value):
            raise HTTPException(status_code=422, detail="answer must be a finite number")
        cleaned = str(raw_value)
    else:
        raise HTTPException(status_code=422, detail="answer must be a number")
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail="answer must be a number") from exc
    if not value.is_finite():
        raise HTTPException(status_code=422, detail="answer must be a finite number")
    return value


def _validate_number(
    schema: Mapping[str, Any],
    raw_value: Any,
    *,
    integer: bool,
) -> int | float:
    value = _decimal(raw_value)
    if integer and value != value.to_integral_value():
        raise HTTPException(status_code=422, detail="answer must be a whole number")

    minimum = schema.get("minimum", schema.get("min"))
    maximum = schema.get("maximum", schema.get("max"))
    if minimum is not None and value < _decimal(minimum):
        raise HTTPException(status_code=422, detail=f"answer must be at least {minimum}")
    if maximum is not None and value > _decimal(maximum):
        raise HTTPException(status_code=422, detail=f"answer must be at most {maximum}")

    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if exclusive_minimum is not None and value <= _decimal(exclusive_minimum):
        raise HTTPException(
            status_code=422,
            detail=f"answer must be greater than {exclusive_minimum}",
        )
    if exclusive_maximum is not None and value >= _decimal(exclusive_maximum):
        raise HTTPException(
            status_code=422,
            detail=f"answer must be less than {exclusive_maximum}",
        )

    multiple = schema.get("multipleOf")
    if multiple is not None:
        step = _decimal(multiple)
        if step <= 0 or value % step != 0:
            raise HTTPException(status_code=422, detail=f"answer must be a multiple of {multiple}")

    normalized: int | float
    normalized = int(value) if integer or value == value.to_integral_value() else float(value)
    _validate_enum(schema, normalized)
    return normalized


def _validate_boolean(schema: Mapping[str, Any], raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        value = raw_value
    elif isinstance(raw_value, str) and raw_value.strip().casefold() in {"true", "yes"}:
        value = True
    elif isinstance(raw_value, str) and raw_value.strip().casefold() in {"false", "no"}:
        value = False
    else:
        raise HTTPException(status_code=422, detail="answer must be true/false")
    _validate_enum(schema, value)
    return value


def _validate_enum(schema: Mapping[str, Any], value: Any) -> None:
    allowed = schema.get("enum")
    if isinstance(allowed, list) and allowed and value not in allowed:
        rendered = ", ".join(str(item) for item in allowed)
        raise HTTPException(status_code=422, detail=f"answer must be one of: {rendered}")


def recruiter_input_allows_dismiss(row: AgentNeedsInput) -> bool:
    """Return whether the live question contract permits dismissal.

    Kept public so every write surface (typed Agent Chat and the HTTP card
    endpoint) enforces the same ``allow_dismiss`` / ``dismissible`` semantics.
    """

    schema = _schema(row)
    marker = schema.get("allow_dismiss", schema.get("dismissible", True))
    if isinstance(marker, str):
        return marker.strip().casefold() not in {"false", "no", "0", "required"}
    return bool(marker)


__all__ = [
    "MAX_LIST_LIMIT",
    "RECRUITER_INPUT_TOOL_DEFINITIONS",
    "answer_recruiter_input",
    "dismiss_recruiter_input",
    "list_open_recruiter_inputs",
    "recruiter_input_allows_dismiss",
    "recruiter_input_contract",
]
