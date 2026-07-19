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

from typing import Any, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..models.role_brief import RoleBrief
from .requisition_chat_capture_support import (
    _STANDARD_KEY_INTRINSIC_TYPE,
    _brief_value_for_field as _brief_value_for_field,
    _coerce_value,
    _field_by_key,
    _field_type_index,
    _first_required_field as _first_required_field,
    _is_empty,
    _select_options,
    compute_completeness,
    compute_gaps,
    opening_message as opening_message,
)
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
class BriefFieldChange(BaseModel):
    """An intent-aware edit to one template field.

    The model decides what the user meant; the application executes that
    decision predictably.  ``set`` replaces the whole field, while ``add`` and
    ``remove`` preserve the rest of a list.
    """

    key: str
    operation: Literal["set", "add", "remove", "clear", "keep"]
    value: Any = None
    evidence: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class ChatCapture(BaseModel):
    """What the agent emits each turn: a conversational reply, the next
    open questions, and every field value it could capture. All field values
    are optional so a partial turn yields a partial fill."""

    assistant_reply: str
    open_questions: Optional[list[str]] = None
    # Up to ~6 short tappable answers to the question the reply asks — quick
    # replies the recruiter clicks instead of typing. For select fields use the
    # template options verbatim; otherwise offer the most likely answers. EVERY
    # option must answer the SAME single question (never mix fields).
    suggested_replies: Optional[list[str]] = None
    # True when the question accepts SEVERAL of the suggested_replies at once
    # (must-haves, tech stack, focus areas …) → the UI lets the user pick
    # multiple chips and send them together. False/absent = single-choice.
    suggested_multi: Optional[bool] = None

    # How this turn relates to the existing draft.  A replacement resets only
    # template-owned role content; linkage, candidate coupling and audit history
    # live outside the template and are always preserved.  ``clarify`` records
    # no role-field mutations.
    change_mode: Literal["amend", "replace", "clarify"] = "amend"
    changes: Optional[list[BriefFieldChange]] = None
    # The complete post-change JD.  When an active verbatim specification
    # exists, field edits and this canonical document are committed together.
    canonical_job_spec: Optional[str] = None
    # A full document waiting for the user to choose replace vs amend.
    pending_job_spec: Optional[str] = None

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

    # Built-in template fields that intentionally do not have RoleBrief
    # columns.  Keeping these first-class in the tool schema makes document
    # ingestion reliable: the model does not have to remember that core fields
    # such as responsibilities belong inside the catch-all ``custom`` object.
    # ``apply_capture`` still validates them against the resolved template and
    # stores them in ``custom_fields``.
    domain: Optional[str] = None
    urgency: Optional[str] = None
    bonus: Optional[str] = None
    equity: Optional[str] = None
    benefits: Optional[list[str]] = None
    responsibilities: Optional[list[str]] = None

    # Org-template-added keys that have no RoleBrief column → custom_fields.
    custom: Optional[dict[str, Any]] = None


# The set of typed standard keys the tool exposes (column-routable). Anything
# else the model puts in ``custom`` is routed by the template.
_STANDARD_CAPTURE_KEYS = frozenset(
    set(ChatCapture.model_fields)
    - {
        "assistant_reply",
        "open_questions",
        "suggested_replies",
        "suggested_multi",
        "change_mode",
        "changes",
        "canonical_job_spec",
        "pending_job_spec",
        "custom",
    }
)


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


def apply_capture(
    db: Session,
    brief: RoleBrief,
    capture: ChatCapture,
    template: dict[str, Any],
    *,
    transcript_attr: str = "messages",
) -> RoleBrief:
    """Apply the agent's intent-aware change plan and synchronize its active JD."""
    type_index = _field_type_index(template)
    data = capture.model_dump(exclude_none=True)
    open_questions = data.pop("open_questions", None)
    data.pop("assistant_reply", None)
    data.pop("suggested_replies", None)
    data.pop("suggested_multi", None)
    change_mode = str(data.pop("change_mode", "amend"))
    data.pop("changes", None)
    canonical_spec = str(data.pop("canonical_job_spec", "") or "").strip()
    pending_spec = str(data.pop("pending_job_spec", "") or "").strip()
    custom_in = data.pop("custom", None) or {}

    column_updates: dict[str, Any] = {}
    custom_updates: dict[str, Any] = dict(brief.custom_fields or {})
    custom_changed = False
    fields = {field["key"]: field for _section, field in iter_fields(template)}
    state = dict(brief.agent_state or {})
    state_changed = False
    client_scope = transcript_attr == "client_messages"
    active_spec_key = "client_canonical_source" if client_scope else "jd_override"
    mode_key = "client_canonical_spec_mode" if client_scope else "canonical_spec_mode"
    pending_key = (
        "client_pending_job_spec_source" if client_scope else "pending_job_spec_source"
    )

    def write_field(key: str, value: Any, *, clear: bool = False) -> None:
        nonlocal custom_changed
        column = template_key_to_column(key)
        if column is not None:
            column_updates[column] = value
        elif clear:
            if key in custom_updates:
                custom_updates.pop(key, None)
                custom_changed = True
        else:
            custom_updates[key] = value
            custom_changed = True

    def current_value(key: str) -> Any:
        column = template_key_to_column(key)
        if column is not None:
            return column_updates.get(column, getattr(brief, column, None))
        return custom_updates.get(key)

    def field_type(key: str) -> str:
        return _STANDARD_KEY_INTRINSIC_TYPE.get(key, type_index.get(key, "text"))

    def list_identity(value: Any) -> str:
        if isinstance(value, str):
            return value.strip().casefold()
        return repr(value)

    # A clarification is deliberately non-mutating.  Keep the proposed source
    # available for the user's next "replace" / "apply differences" answer.
    if change_mode == "clarify":
        if pending_spec:
            state[pending_key] = pending_spec[:100_000]
            state_changed = True
        if open_questions is not None:
            state["open_questions"] = open_questions
            state_changed = True
        if state_changed:
            update_brief_fields(db, brief, agent_state=state)
        brief.completeness = compute_completeness(brief, template)
        db.flush()
        return brief

    # Replacement applies only to template-owned role content.  Operational
    # linkage (source_role_id, candidate coupling, owner, messages) is untouched.
    if change_mode == "replace":
        for key, field in fields.items():
            f_type = field_type(key)
            write_field(
                key,
                [] if f_type in ("list", "struct_list") else None,
                clear=template_key_to_column(key) is None,
            )

    # Standard typed keys → mapped column. Coerce by the key's INTRINSIC type
    # (the capture schema already guarantees the shape), not the org template's
    # declared type — re-coercing a typed dict/list to a scalar would corrupt it.
    for key in list(data.keys()):
        if key not in _STANDARD_CAPTURE_KEYS:
            continue
        # A first-class capture is still accepted only when the resolved org
        # template contains that key.  This prevents built-in convenience
        # fields from leaking into an org that deliberately removed them.
        if key not in type_index:
            continue
        column = template_key_to_column(key)
        f_type = _STANDARD_KEY_INTRINSIC_TYPE.get(key, "text")
        coerced = _coerce_value(data[key], f_type)
        if _is_empty(coerced):
            continue
        if column is not None:
            column_updates[column] = coerced
        else:
            custom_updates[key] = coerced
            custom_changed = True

    # Custom keys (org-template-added). Route by the template: a custom key that
    # actually maps to a column still writes to the column; otherwise it lands
    # in custom_fields. Unknown keys (not in the template at all) are ignored.
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

    # Explicit operations run after direct extraction, so an "add" or "remove"
    # is applied to the final value assembled for this turn rather than blindly
    # replacing the whole list.
    for change in capture.changes or []:
        key = str(change.key or "").strip()
        if key not in fields or change.operation == "keep":
            continue
        f_type = field_type(key)
        if change.operation == "clear":
            write_field(
                key,
                [] if f_type in ("list", "struct_list") else None,
                clear=template_key_to_column(key) is None,
            )
            continue
        if change.operation == "set":
            coerced = _coerce_value(change.value, f_type)
            if coerced is None:
                if _is_empty(change.value):
                    write_field(
                        key,
                        [] if f_type in ("list", "struct_list") else None,
                        clear=template_key_to_column(key) is None,
                    )
                continue
            write_field(key, coerced)
            continue
        if f_type not in ("list", "struct_list"):
            continue
        incoming = _coerce_value(change.value, f_type) or []
        existing = list(current_value(key) or [])
        incoming_ids = {list_identity(item) for item in incoming}
        if change.operation == "add":
            known = {list_identity(item) for item in existing}
            updated = list(existing)
            for item in incoming:
                identity = list_identity(item)
                if identity not in known:
                    updated.append(item)
                    known.add(identity)
        else:  # remove
            updated = [
                item for item in existing if list_identity(item) not in incoming_ids
            ]
        write_field(key, updated)

    if custom_changed:
        column_updates["custom_fields"] = custom_updates

    structured_changed = any(
        getattr(brief, key, None) != value
        for key, value in column_updates.items()
        if key != "custom_fields"
    ) or (
        "custom_fields" in column_updates
        and dict(brief.custom_fields or {}) != custom_updates
    )

    # Commit the complete canonical JD with the structured edit.  If the model
    # ever omits the required revised document, drop the stale verbatim override
    # and fall back to the template-rendered Brief rather than publishing text
    # that contradicts the scoring fields.  ``raw_input`` retains provenance.
    previous_override = str(state.get(active_spec_key) or "").strip()
    visible_override = str(state.get("jd_override") or "").strip()
    previous_mode = state.get(mode_key)
    canonical_changed = False
    if canonical_spec:
        canonical_changed = canonical_spec != previous_override
        state[active_spec_key] = canonical_spec[:100_000]
        state[mode_key] = "verbatim"
        # The client-scoped canonical source is private model context, but its
        # accepted revision is also the one visible/published by the recruiter.
        if client_scope:
            state["jd_override"] = canonical_spec[:100_000]
            state["canonical_spec_mode"] = "verbatim"
        state_changed = previous_mode != "verbatim" or canonical_changed
    elif structured_changed and (
        previous_override or visible_override or change_mode == "replace"
    ):
        state.pop(active_spec_key, None)
        state[mode_key] = "structured"
        if client_scope:
            state.pop("jd_override", None)
            state["canonical_spec_mode"] = "structured"
        canonical_changed = True
    if canonical_changed:
        try:
            revision = int(state.get("job_spec_revision") or 0) + 1
        except (TypeError, ValueError):
            revision = 1
        state["job_spec_revision"] = revision
        state["job_spec_last_change_mode"] = change_mode
        state_changed = True
    if pending_key in state:
        state.pop(pending_key, None)
        state_changed = True
    if open_questions is not None:
        state["open_questions"] = open_questions
        state_changed = True
    if state_changed:
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
    review-ready nudge with no options. The deterministic chat recorder never
    implies that it can perform the separate publish action."""
    gaps = compute_gaps(brief, template)
    if not gaps:
        return ("That's everything I need — the brief is ready for review.", [])
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
