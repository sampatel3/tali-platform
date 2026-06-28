"""Requisition intake — the deterministic CAPTURE core (no LLM, no Anthropic).

The pieces that turn a template + a brief into gaps/completeness, and that fold
captured values back into the brief:

  * ``ChatCapture`` — the forced-tool-use schema the agent emits each turn.
  * gap engine + ``compute_completeness`` over the template's required fields.
  * ``apply_capture`` — route + coerce captured values onto brief columns /
    ``custom_fields`` (never blanking previously-captured data).
  * ``record_answer`` / ``next_gap_prompt`` — the deterministic single-answer
    path that powers the free ``/answer`` endpoint.

Split out of ``requisition_chat_service`` (the turn engine), which re-exports
these names so the public import path is unchanged. All of this is unit-tested
without a DB-or-LLM-free where possible.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..models.role_brief import RoleBrief
from .requisition_intake_agent import (
    CalibrationExemplar,
    WeightedPriority,
)
from .requisition_template_service import (
    iter_fields,
    template_key_to_column,
)
from .role_brief_service import update_brief_fields


# --------------------------------------------------------------------------- #
# The capture tool schema — typed optional fields for the standard RoleBrief
# columns (robust common path) + an open ``custom`` dict for org-added keys.
# --------------------------------------------------------------------------- #
class ChatCapture(BaseModel):
    """What the agent emits each turn: a conversational reply, the next
    open questions, and every field value it could capture. All field values
    are optional so a partial turn yields a partial fill."""

    assistant_reply: str
    open_questions: Optional[list[str]] = None
    # Up to ~6 short tappable answers to the question the reply asks — quick
    # replies the recruiter clicks instead of typing. For select fields use the
    # template options verbatim; otherwise offer the most likely answers.
    suggested_replies: Optional[list[str]] = None

    # Standard RoleBrief columns (typed).
    title: Optional[str] = None
    department: Optional[str] = None
    seniority: Optional[str] = None
    summary: Optional[str] = None
    location_city: Optional[str] = None
    location_country: Optional[str] = None
    workplace_type: Optional[str] = None
    employment_type: Optional[str] = None
    openings: Optional[int] = None
    target_start_date: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    must_haves: Optional[list[str]] = None
    preferred: Optional[list[str]] = None
    dealbreakers: Optional[list[str]] = None
    success_profile: Optional[str] = None
    priorities: Optional[list[WeightedPriority]] = None
    tradeoffs: Optional[list[str]] = None
    calibration_exemplars: Optional[list[CalibrationExemplar]] = None
    sourcing_signals: Optional[list[str]] = None
    assessment_focus: Optional[list[str]] = None
    process: Optional[str] = None
    evp: Optional[list[str]] = None

    # Org-template-added keys that have no RoleBrief column → custom_fields.
    custom: Optional[dict[str, Any]] = None


# The set of typed standard keys the tool exposes (column-routable). Anything
# else the model puts in ``custom`` is routed by the template.
_STANDARD_CAPTURE_KEYS = frozenset(
    set(ChatCapture.model_fields) - {"assistant_reply", "open_questions", "custom"}
)


# --------------------------------------------------------------------------- #
# Deterministic helpers (no LLM, no DB) — gap engine, opening message,
# completeness, value coercion.
# --------------------------------------------------------------------------- #
def _is_empty(value: Any) -> bool:
    """A field counts as unfilled when None, empty string/whitespace, or an
    empty list/dict."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _brief_value_for_field(brief: RoleBrief, field_key: str) -> Any:
    """Current brief value for a template field key: the mapped column, else
    ``custom_fields[key]``."""
    column = template_key_to_column(field_key)
    if column is not None:
        return getattr(brief, column, None)
    return (brief.custom_fields or {}).get(field_key)


def compute_gaps(brief: RoleBrief, template: dict[str, Any]) -> list[dict[str, str]]:
    """Required template fields whose brief value is empty/blank, in template
    order. Each gap is ``{key, label, section}``."""
    gaps: list[dict[str, str]] = []
    for section, field in iter_fields(template):
        if not field.get("required"):
            continue
        if _is_empty(_brief_value_for_field(brief, field["key"])):
            gaps.append(
                {
                    "key": field["key"],
                    "label": field.get("label") or field["key"],
                    "section": section.get("key") or "",
                }
            )
    return gaps


def compute_completeness(brief: RoleBrief, template: dict[str, Any]) -> int:
    """``round(100 * filled_required / total_required)`` over required fields.
    100 when the template has no required fields."""
    total = 0
    filled = 0
    for _section, field in iter_fields(template):
        if not field.get("required"):
            continue
        total += 1
        if not _is_empty(_brief_value_for_field(brief, field["key"])):
            filled += 1
    if total == 0:
        return 100
    return round(100 * filled / total)


def _field_by_key(template: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    for _section, field in iter_fields(template):
        if field.get("key") == key:
            return field
    return None


def _first_required_field(template: dict[str, Any]) -> Optional[dict[str, Any]]:
    for _section, field in iter_fields(template):
        if field.get("required"):
            return field
    return None


def _select_options(field: Optional[dict[str, Any]]) -> list[str]:
    """The tappable options for a select field (else empty)."""
    if field and field.get("type") == "select":
        return [str(o) for o in (field.get("options") or []) if str(o).strip()][:6]
    return []


def _resolve_suggested_replies(
    capture: "ChatCapture", brief: RoleBrief, template: dict[str, Any]
) -> list[str]:
    """Quick replies to offer with the agent's turn: what the model gave, else
    the template options of the next required gap (so select questions are
    always tappable even if the model forgets to populate them)."""
    replies = [str(r).strip() for r in (capture.suggested_replies or []) if str(r).strip()]
    if replies:
        return replies[:6]
    gaps = compute_gaps(brief, template)
    if gaps:
        return _select_options(_field_by_key(template, gaps[0]["key"]))
    return []


def opening_message(template: dict[str, Any]) -> str:
    """Deterministic greeting + the first required field's question."""
    first_question = "what role are you hiring for?"
    for _section, field in iter_fields(template):
        if field.get("required"):
            q = (field.get("question") or "").strip()
            if q:
                first_question = q[0].lower() + q[1:] if q else first_question
            break
    return (
        "Hi — I'll help you spec this role fast. You can talk to me, paste your "
        "kickoff-call notes, or drop a transcript or screenshot. To start: "
        + first_question
    )


def _field_type_index(template: dict[str, Any]) -> dict[str, str]:
    """Map every template field key → its declared type (for coercion)."""
    return {field["key"]: field.get("type", "text") for _s, field in iter_fields(template)}


def _coerce_value(value: Any, field_type: str) -> Any:
    """Coerce a captured value per the template field type. Returns ``None``
    when the value can't be coerced / is empty (so it never overwrites)."""
    if value is None:
        return None
    if field_type == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip().replace(",", "")
        if text == "":
            return None
        if text.lstrip("-").isdigit():
            return int(text)
        try:
            return float(text)
        except (TypeError, ValueError):
            pass
        # Natural-language answer / quick-reply chip like "2 openings",
        # "around 3", "AED 200k" — pull the first number out rather than
        # rejecting the whole answer (which 422s the deterministic recorder).
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if match is None:
            return None
        num = match.group(0)
        return float(num) if "." in num else int(num)
    if field_type == "list":
        if isinstance(value, list):
            out = [str(v).strip() for v in value if str(v).strip()]
            return out or None
        text = str(value).strip()
        return [text] if text else None
    if field_type == "struct_list":
        if isinstance(value, list):
            out = []
            for item in value:
                if isinstance(item, BaseModel):
                    out.append(item.model_dump(exclude_none=True))
                elif isinstance(item, dict):
                    out.append(item)
                elif str(item).strip():
                    out.append({"value": str(item).strip()})
            return out or None
        return None
    if field_type in ("text", "longtext", "date", "select"):
        if isinstance(value, (dict, list)):
            return value or None
        text = str(value).strip()
        return text or None
    if field_type == "json":
        # Dict-shaped standard columns (sourcing_signals, process): store the
        # already-correctly-shaped value as-is.
        return value if not _is_empty(value) else None
    # Unknown type → pass through if non-empty.
    return value if not _is_empty(value) else None


# The intrinsic shape of each STANDARD capture key, derived from its Pydantic
# type — NOT from the org template's declared field type. The capture schema
# already guarantees these shapes, so re-coercing a typed dict/list-of-dict to
# whatever scalar type a template author happened to declare would corrupt it.
# Custom (org-added) keys still coerce by the template's declared type.
_STANDARD_KEY_INTRINSIC_TYPE: dict[str, str] = {
    "openings": "number",
    "salary_min": "number",
    "salary_max": "number",
    "must_haves": "list",
    "preferred": "list",
    "dealbreakers": "list",
    "tradeoffs": "list",
    "assessment_focus": "list",
    "evp": "list",
    "priorities": "struct_list",
    "calibration_exemplars": "struct_list",
    "sourcing_signals": "list",
    "process": "longtext",
    # everything else (title, summary, department, seniority, location_*,
    # workplace_type, employment_type, salary_currency, salary_period,
    # success_profile, target_start_date) is scalar text/date/select.
}


def apply_capture(
    db: Session, brief: RoleBrief, capture: ChatCapture, template: dict[str, Any]
) -> RoleBrief:
    """Fold a capture into the brief: route each provided value to its column
    or ``custom_fields`` with per-type coercion, only OVERWRITING when the new
    value is non-empty; persist ``open_questions`` into ``agent_state``;
    recompute ``completeness``. Flushes via ``update_brief_fields``."""
    type_index = _field_type_index(template)
    data = capture.model_dump(exclude_none=True)
    open_questions = data.pop("open_questions", None)
    data.pop("assistant_reply", None)
    custom_in = data.pop("custom", None) or {}

    column_updates: dict[str, Any] = {}

    # Standard typed keys → mapped column. Coerce by the key's INTRINSIC type
    # (the capture schema already guarantees the shape), not the org template's
    # declared type — re-coercing a typed dict/list to a scalar would corrupt it.
    for key in list(data.keys()):
        if key not in _STANDARD_CAPTURE_KEYS:
            continue
        column = template_key_to_column(key)
        if column is None:
            continue
        f_type = _STANDARD_KEY_INTRINSIC_TYPE.get(key, "text")
        coerced = _coerce_value(data[key], f_type)
        if not _is_empty(coerced):
            column_updates[column] = coerced

    # Custom keys (org-template-added). Route by the template: a custom key that
    # actually maps to a column still writes to the column; otherwise it lands
    # in custom_fields. Unknown keys (not in the template at all) are ignored.
    custom_updates: dict[str, Any] = dict(brief.custom_fields or {})
    custom_changed = False
    for key, raw in custom_in.items():
        if key not in type_index:
            continue  # not part of the resolved template
        f_type = type_index[key]
        coerced = _coerce_value(raw, f_type)
        if _is_empty(coerced):
            continue
        column = template_key_to_column(key)
        if column is not None:
            column_updates[column] = coerced
        else:
            custom_updates[key] = coerced
            custom_changed = True

    if custom_changed:
        column_updates["custom_fields"] = custom_updates

    if open_questions is not None:
        state = dict(brief.agent_state or {})
        state["open_questions"] = open_questions
        column_updates["agent_state"] = state

    if column_updates:
        update_brief_fields(db, brief, **column_updates)

    # Recompute completeness AFTER applying so the number reflects this turn.
    brief.completeness = compute_completeness(brief, template)
    db.flush()
    return brief


# --------------------------------------------------------------------------- #
# Deterministic single-answer capture (NO LLM, NO metering) — powers the
# ``/answer`` endpoint so tapping a quick-reply records one field for free.
# --------------------------------------------------------------------------- #
def next_gap_prompt(
    template: dict[str, Any], brief: RoleBrief
) -> tuple[str, list[str]]:
    """The question + tappable options for the FIRST remaining required gap.

    Returns ``(question, options)`` for the first gap from ``compute_gaps`` — its
    template ``question`` (falling back to its label) and, for a select field,
    its ``options`` (else ``[]``). When there are NO gaps left, returns a
    publish nudge with no options."""
    gaps = compute_gaps(brief, template)
    if not gaps:
        return ("That's everything I need — want to publish this?", [])
    field = _field_by_key(template, gaps[0]["key"])
    question = ""
    if field:
        question = (field.get("question") or field.get("label") or "").strip()
    question = question or gaps[0]["label"]
    return (question, _select_options(field))


def record_answer(
    db: Session,
    brief: RoleBrief,
    template: dict[str, Any],
    field_key: str,
    value: Any,
) -> None:
    """Deterministically record ONE field answer onto the brief — no LLM, no
    metering. Coerces ``value`` by the field's declared template type, routes it
    to its column (via ``update_brief_fields``) or into ``custom_fields``, then
    recomputes ``completeness`` and flushes.

    Raises ``HTTPException(422)`` when ``field_key`` isn't a field in the
    template, or when the coerced value is empty (nothing to record)."""
    field = _field_by_key(template, field_key)
    if field is None:
        raise HTTPException(
            status_code=422, detail=f"Unknown requisition field {field_key!r}"
        )
    coerced = _coerce_value(value, field.get("type", "text"))
    if _is_empty(coerced):
        raise HTTPException(
            status_code=422, detail=f"Empty value for field {field_key!r}"
        )
    column = template_key_to_column(field_key)
    if column is not None:
        update_brief_fields(db, brief, **{column: coerced})
    else:
        update_brief_fields(
            db,
            brief,
            custom_fields={**(brief.custom_fields or {}), field_key: coerced},
        )
    # Recompute completeness AFTER applying so it reflects this answer.
    brief.completeness = compute_completeness(brief, template)
    db.flush()
