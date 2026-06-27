"""Conversational requisition intake — one chat turn at a time.

The recruiter / hiring manager *talks* to Taali to capture a complete hiring
spec. Each turn:

  1. append the user message (+ attachment metadata) to ``brief.messages``;
  2. run the deterministic GAP ENGINE against the org's spec template
     (``requisition_template_service``) — which required fields are still empty;
  3. make ONE metered, forced-tool-use LLM call (vision-capable: images become
     base64 blocks, transcripts/PDFs are decoded into the user text) that both
     CAPTURES field values and writes a conversational reply;
  4. apply the captured values to brief columns / ``custom_fields`` with
     per-template-field-type coercion (never blanking previously-captured data),
     recompute ``completeness``, append the assistant reply, persist
     ``open_questions`` into ``agent_state``;
  5. return ``{brief, reply, messages, gaps}`` (gaps recomputed after applying).

The pure pieces (gap engine, opening message, attachment assembly, value
coercion/apply, completeness) are unit-tested without an LLM; the single LLM
call goes through ``app.llm.structured.generate_structured`` (forced tool-use),
which forwards ``messages`` — including image content blocks — unchanged to the
metered Anthropic client, so vision works and every call is billed + logged.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..llm.core import MeteringContext
from ..llm.structured import generate_structured
from ..models.role import Role
from ..models.role_brief import RoleBrief
from ..platform.config import settings
from .claude_client_resolver import get_metered_client
from .requisition_intake_agent import (
    CalibrationExemplar,
    WeightedPriority,
)
from .requisition_template_service import (
    iter_fields,
    resolve_template,
    template_key_to_column,
)
from .role_brief_service import update_brief_fields

logger = logging.getLogger("taali.requisition_chat")

_CHAT_FEATURE = "requisition_intake_chat"
_MAX_TOKENS = 4000
_FOCUS_GAP_COUNT = 3

# AI-draft "What you'll do" — how many concrete responsibility statements we ask
# for, and the custom_fields key they land in (no RoleBrief column → custom).
_RESPONSIBILITIES_KEY = "responsibilities"
_RESPONSIBILITIES_MIN = 6
_RESPONSIBILITIES_MAX = 10
_DRAFT_MAX_TOKENS = 1200

# Warm-start: the brief columns we prefill on a new requisition from the org's
# recent specs (location/workplace/employment/department recur across roles).
_WARM_START_FIELDS = (
    "location_city",
    "location_country",
    "workplace_type",
    "employment_type",
    "department",
)
# How many recent role titles we surface to the agent as warm-start context.
_RECENT_ROLE_TITLES = 5

# Warm-start from the org's REAL history lives in ``roles.workable_job_data``
# (the structured Workable job payload). Its ``workplace_type`` /
# ``employment_type`` come in Workable's snake_case vocabulary, but the
# requisition template's select OPTIONS are the nice human labels — normalise
# one to the other (case/format-insensitive) so a prefilled value is a valid
# template option and the gap engine / select chips treat it as captured.
_WORKPLACE_TYPE_LABELS: dict[str, str] = {
    "onsite": "Onsite",
    "on_site": "Onsite",
    "on-site": "Onsite",
    "office": "Onsite",
    "hybrid": "Hybrid",
    "remote": "Remote",
}
_EMPLOYMENT_TYPE_LABELS: dict[str, str] = {
    "full_time": "Full-time",
    "full-time": "Full-time",
    "fulltime": "Full-time",
    "part_time": "Part-time",
    "part-time": "Part-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "contractor": "Contract",
    "temporary": "Temporary",
    "temp": "Temporary",
}

# Extensions we treat as decode-able text/transcripts (appended to the user
# message inline so the model reads them as conversation context).
_TEXT_EXTENSIONS = {"txt", "vtt", "srt", "md", "markdown", "text"}
# Anthropic image block media types we pass through for vision.
_SUPPORTED_IMAGE_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}


# --------------------------------------------------------------------------- #
# Attachment metadata (what we persist on the message) + the upload view the
# route hands us (decoupled from FastAPI's UploadFile so the assembly logic is
# unit-testable with plain objects).
# --------------------------------------------------------------------------- #
class ChatAttachment(BaseModel):
    """One uploaded file, already read into memory by the route."""

    name: str
    content_type: Optional[str] = None
    content: bytes = b""


def _attachment_kind(att: ChatAttachment) -> str:
    """Coarse kind stored on the persisted message + used to label content."""
    ctype = (att.content_type or "").lower()
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    if ctype.startswith("image/"):
        return "image"
    if ctype.startswith("text/") or ext in _TEXT_EXTENSIONS:
        return "transcript"
    return "file"


def _image_media_type(att: ChatAttachment) -> Optional[str]:
    ctype = (att.content_type or "").lower().split(";")[0].strip()
    if ctype in _SUPPORTED_IMAGE_MEDIA:
        return ctype
    # Fall back to extension for clients that send octet-stream.
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext)


def _decode_text_attachment(att: ChatAttachment) -> Optional[str]:
    try:
        return att.content.decode("utf-8", errors="replace").strip() or None
    except Exception:  # pragma: no cover — defensive
        return None


def _decode_pdf_attachment(att: ChatAttachment) -> Optional[str]:
    """Extract text from a PDF if the repo's extractor is available; else None."""
    try:
        from .document_service import extract_text

        text = extract_text(att.content, "pdf")
        return (text or "").strip() or None
    except Exception as exc:  # pragma: no cover — defensive
        logger.info("requisition chat: PDF extraction failed for %s: %s", att.name, exc)
        return None


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


class ResponsibilitiesDraft(BaseModel):
    """The AI-drafted "What you'll do" list: 6–10 concrete responsibility
    statements, each a short action phrase starting with a verb."""

    responsibilities: list[str]


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
        try:
            text = str(value).strip().replace(",", "")
            if text == "":
                return None
            return int(text) if text.lstrip("-").isdigit() else float(text)
        except (TypeError, ValueError):
            return None
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


# --------------------------------------------------------------------------- #
# Persisted-message + LLM-input assembly.
# --------------------------------------------------------------------------- #
def build_persisted_user_message(
    text: str, attachments: list[ChatAttachment]
) -> dict[str, Any]:
    """The user turn we store on ``brief.messages`` (text + attachment metadata,
    NOT the raw bytes)."""
    return {
        "role": "user",
        "content": text or "",
        "attachments": [
            {"name": a.name, "kind": _attachment_kind(a)} for a in attachments
        ],
    }


def _history_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map persisted messages → Anthropic message dicts (text only). The newest
    user turn is rebuilt separately so attachments become real content blocks."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            content = str(content or "")
        out.append({"role": role, "content": content})
    return out


def build_user_turn_content(
    text: str, attachments: list[ChatAttachment]
) -> Any:
    """Build the content for the NEW user turn sent to the model.

    Transcripts/PDFs are decoded and appended to the text labelled
    ``[Attached transcript: <name>]\\n<content>``; images become base64 image
    blocks (vision). Returns a plain string when there are no image blocks (so
    text-only turns stay simple), else a list of content blocks.
    """
    text_parts: list[str] = []
    if (text or "").strip():
        text_parts.append(text.strip())

    image_blocks: list[dict[str, Any]] = []
    for att in attachments:
        kind = _attachment_kind(att)
        if kind == "image":
            media_type = _image_media_type(att)
            if media_type and att.content:
                image_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(att.content).decode("ascii"),
                        },
                    }
                )
            else:
                text_parts.append(f"[Attached image: {att.name} — could not be read]")
        elif kind == "transcript":
            decoded = _decode_text_attachment(att)
            if decoded:
                text_parts.append(f"[Attached transcript: {att.name}]\n{decoded}")
            else:
                text_parts.append(f"[Attached transcript: {att.name} — empty]")
        else:  # file
            ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
            ctype = (att.content_type or "").lower()
            if ext == "pdf" or "pdf" in ctype:
                decoded = _decode_pdf_attachment(att)
                if decoded:
                    text_parts.append(f"[Attached document: {att.name}]\n{decoded}")
                else:
                    text_parts.append(
                        f"[Attached document: {att.name} — PDF text could not be extracted]"
                    )
            else:
                text_parts.append(f"[Attached file: {att.name} — unsupported type, skipped]")

    joined = "\n\n".join(text_parts).strip()
    if not image_blocks:
        return joined or "(no message)"
    blocks: list[dict[str, Any]] = []
    if joined:
        blocks.append({"type": "text", "text": joined})
    blocks.extend(image_blocks)
    return blocks


def _captured_brief_values(brief: RoleBrief, template: dict[str, Any]) -> dict[str, Any]:
    """Non-empty current brief values keyed by template field key (for the
    system prompt's 'captured so far')."""
    out: dict[str, Any] = {}
    for _section, field in iter_fields(template):
        value = _brief_value_for_field(brief, field["key"])
        if not _is_empty(value):
            out[field["key"]] = value
    return out


def build_chat_system_prompt(
    brief: RoleBrief,
    template: dict[str, Any],
    focus_gaps: list[dict[str, str]],
    recent_titles: Optional[list[str]] = None,
    *,
    client_org_name: Optional[str] = None,
) -> str:
    """The system prompt: template + captured-so-far + focus gaps (+ a compact
    recent-roles line for warm-start context when ``recent_titles`` is given).

    When ``client_org_name`` is set the prompt is CLIENT-FRAMED: the speaker is
    the consultancy's client describing a role they want ``{org}`` to hire for,
    the agent captures the role + its requirements, and it must NEVER ask about
    salary / compensation / budget — the consultancy owns economics. (The
    client-scoped template already has the compensation section removed; the
    instruction makes the boundary explicit so the agent never volunteers a
    pay question either.)"""
    captured = _captured_brief_values(brief, template)
    # Compact template: just the structure the model needs to fill.
    compact_template = {
        "sections": [
            {
                "key": s.get("key"),
                "fields": [
                    {
                        "key": f.get("key"),
                        "label": f.get("label"),
                        "type": f.get("type"),
                        "required": bool(f.get("required")),
                        **({"options": f["options"]} if f.get("options") else {}),
                    }
                    for f in (s.get("fields") or [])
                ],
            }
            for s in (template.get("sections") or [])
        ]
    }
    focus_lines = "\n".join(
        f"- {g['label']}: {_question_for_gap(template, g['key'])}" for g in focus_gaps
    ) or "- (the spec looks complete)"
    recent_clean = [str(t).strip() for t in (recent_titles or []) if str(t).strip()]
    recent_line = (
        f"\n\nFor context, recent roles at this org: {', '.join(recent_clean)}."
        if recent_clean
        else ""
    )
    org = (client_org_name or "").strip()
    if org:
        # CLIENT-framed intro + a hard no-pay-questions instruction. The
        # speaker is the consultancy's client, not an internal recruiter.
        intro = (
            f"You are {org}'s requisition intake agent, helping {org}'s CLIENT "
            f"describe a role they want {org} to hire for. Capture the role and "
            "its requirements. Here is the spec template you must fill: "
        )
        comp_instruction = (
            "Do NOT ask about salary, compensation, or budget — "
            f"{org}'s team handles that; never raise pay even if prompted. "
        )
        closing = (
            "ALWAYS keep momentum: every reply asks the next most useful "
            "question, or — once the role is captured — thanks them and says "
            f"{org}'s team will take it from here. "
        )
    else:
        intro = (
            "You are Taali's requisition intake agent, helping a recruiter or "
            "hiring manager capture a complete hiring spec by talking. Here is "
            "the org's spec template you must fill: "
        )
        comp_instruction = (
            "Salary is in AED by default — don't ask about currency unless the "
            "recruiter raises it. "
        )
        closing = (
            "ALWAYS keep momentum: every reply asks the next most useful "
            "question, or — once the required spec is captured — says so and "
            "offers to publish. "
        )
    return (
        intro
        + json.dumps(compact_template, separators=(",", ":"))
        + "\n\nCaptured so far: "
        + json.dumps(captured, separators=(",", ":"), default=str)
        + "\n\nMost important gaps to close next:\n"
        + focus_lines
        + recent_line
        + "\n\nFrom the user's message and any attached transcript/screenshot, "
        "capture every field you can — use the typed fields for standard columns "
        "and the 'custom' object for any other template key (e.g. 'urgency'); "
        "never skip a field just because it isn't a typed column. "
        + comp_instruction
        + "Then reply "
        "conversationally — warm, concise, fast — acknowledging what you got and "
        "asking about the focus gaps next (one or two at a time, never "
        "interrogate). "
        + closing
        + "ALWAYS set suggested_replies to up to 6 short, "
        "tappable options for the question you ask: for select fields use the "
        "template's options verbatim; for numbers, dates or free text offer the "
        "most likely answers or sensible ranges (they can still type anything)."
    )


def _question_for_gap(template: dict[str, Any], field_key: str) -> str:
    for _s, field in iter_fields(template):
        if field.get("key") == field_key:
            return (field.get("question") or field.get("label") or field_key)
    return field_key


# --------------------------------------------------------------------------- #
# Warm-start: prefill a new requisition from the org's recent specs.
# --------------------------------------------------------------------------- #
def _warm_start_from_briefs(
    db: Session, organization_id: int, exclude_brief_id: Optional[int] = None
) -> dict[str, Any]:
    """The most-recent non-empty value for each warm-start field across the org's
    RoleBriefs (recency-biased).

    For each of ``location_city / location_country / workplace_type /
    employment_type / department`` independently, walk the org's briefs newest
    first (``created_at`` desc, then ``id`` desc) and take the first non-empty
    value. Optionally exclude one brief (the just-created one). Returns only the
    keys that resolved to a value.
    """
    query = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == organization_id)
        .order_by(RoleBrief.created_at.desc(), RoleBrief.id.desc())
    )
    if exclude_brief_id is not None:
        query = query.filter(RoleBrief.id != exclude_brief_id)

    resolved: dict[str, Any] = {}
    remaining = set(_WARM_START_FIELDS)
    for prior in query.all():
        if not remaining:
            break
        for field in list(remaining):
            value = getattr(prior, field, None)
            if not _is_empty(value):
                resolved[field] = value
                remaining.discard(field)
    return resolved


def _norm_select(value: Any, labels: dict[str, str]) -> Optional[str]:
    """Normalise a raw select value to its template label via ``labels`` (a
    lower-cased lookup). Tolerates already-nice values (e.g. ``"Hybrid"``,
    ``"Full-time"``) by matching case-insensitively against the label set too.
    Returns ``None`` for empty / unrecognised input."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    mapped = labels.get(key)
    if mapped is not None:
        return mapped
    # Already a nice label (or differently-cased one)? Accept it verbatim.
    for label in labels.values():
        if label.lower() == key:
            return label
    return None


def _warm_start_from_job_data(wjd: dict[str, Any]) -> dict[str, Any]:
    """Pull the warm-start fields out of ONE role's ``workable_job_data`` payload,
    normalised to the requisition template's shapes. Robust to missing keys, a
    non-dict ``location``, and unexpected value types — a field that can't be
    derived is simply absent from the result (never raises)."""
    out: dict[str, Any] = {}

    workplace = _norm_select(wjd.get("workplace_type"), _WORKPLACE_TYPE_LABELS)
    if workplace is not None:
        out["workplace_type"] = workplace

    employment = _norm_select(wjd.get("employment_type"), _EMPLOYMENT_TYPE_LABELS)
    if employment is not None:
        out["employment_type"] = employment

    # Department is often null on Workable jobs — only take a non-empty string.
    department = wjd.get("department")
    if isinstance(department, str) and department.strip():
        out["department"] = department.strip()

    # Location: prefer the structured city/country; fall back to splitting the
    # human "City, …, Country" ``location_str`` when the structured keys are
    # absent (matches the prod shape, where location_str is always present).
    location = wjd.get("location")
    city = country = None
    location_str = None
    if isinstance(location, dict):
        raw_city = location.get("city") or location.get("city_name")
        raw_country = location.get("country") or location.get("country_name")
        if isinstance(raw_city, str) and raw_city.strip():
            city = raw_city.strip()
        if isinstance(raw_country, str) and raw_country.strip():
            country = raw_country.strip()
        raw_str = location.get("location_str")
        if isinstance(raw_str, str) and raw_str.strip():
            location_str = raw_str.strip()
    elif isinstance(location, str) and location.strip():
        location_str = location.strip()

    if (city is None or country is None) and location_str:
        parts = [p.strip() for p in location_str.split(", ") if p.strip()]
        if parts:
            if city is None:
                city = parts[0]
            if country is None and len(parts) > 1:
                country = parts[-1]

    if city:
        out["location_city"] = city
    if country:
        out["location_country"] = country

    return out


def warm_start_from_roles(
    db: Session, organization_id: int
) -> dict[str, Any]:
    """Recency-biased warm-start derived from the org's REAL specs — its
    non-deleted ``Role`` rows' ``workable_job_data`` (the structured Workable job
    payload), NOT the near-empty ``role_briefs`` table.

    Walks the org's roles newest first (``created_at`` desc, then ``id`` desc)
    and, for each warm-start field independently
    (``workplace_type / employment_type / location_city / location_country /
    department``), takes the first non-empty value — normalised to the
    requisition template's labels (e.g. ``"hybrid"`` → ``"Hybrid"``,
    ``"full_time"`` → ``"Full-time"``). Robust to missing/odd ``workable_job_data``
    (such roles just contribute nothing). Deterministic; no LLM. Returns only the
    keys that resolved to a value.
    """
    query = (
        db.query(Role)
        .filter(
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.created_at.desc(), Role.id.desc())
    )

    resolved: dict[str, Any] = {}
    remaining = set(_WARM_START_FIELDS)
    for role in query.all():
        if not remaining:
            break
        wjd = role.workable_job_data
        if not isinstance(wjd, dict) or not wjd:
            continue
        derived = _warm_start_from_job_data(wjd)
        for field in list(remaining):
            value = derived.get(field)
            if not _is_empty(value):
                resolved[field] = value
                remaining.discard(field)
    return resolved


def warm_start_fields(
    db: Session, organization_id: int, exclude_brief_id: Optional[int] = None
) -> dict[str, Any]:
    """Recency-biased prefill for a NEW requisition, deterministic (no LLM).

    Combines two sources, brief-first: a recruiter's own recent requisitions
    (``RoleBrief`` rows) are the most relevant, so they win per field; any
    warm-start field still empty is then filled from the org's REAL history — its
    recent ``Role`` rows' ``workable_job_data`` (see ``warm_start_from_roles``).
    In practice ``role_briefs`` is near-empty, so most fields come from roles.

    Returns only the keys that resolved to a value (across either source).
    """
    resolved = _warm_start_from_briefs(
        db, organization_id, exclude_brief_id=exclude_brief_id
    )
    if set(resolved) >= set(_WARM_START_FIELDS):
        return resolved
    for field, value in warm_start_from_roles(db, organization_id).items():
        resolved.setdefault(field, value)
    return resolved


def recent_role_titles(
    db: Session, organization_id: int, exclude_brief_id: Optional[int] = None
) -> list[str]:
    """Up to ``_RECENT_ROLE_TITLES`` recent non-empty brief titles for the org
    (newest first), for warm-start context in the agent's system prompt."""
    query = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == organization_id)
        .order_by(RoleBrief.created_at.desc(), RoleBrief.id.desc())
    )
    if exclude_brief_id is not None:
        query = query.filter(RoleBrief.id != exclude_brief_id)

    titles: list[str] = []
    for prior in query.all():
        if len(titles) >= _RECENT_ROLE_TITLES:
            break
        title = (prior.title or "").strip()
        if title:
            titles.append(title)
    return titles


def seed_opening_message(brief: RoleBrief, template: dict[str, Any]) -> None:
    """Set ``brief.messages`` to the single deterministic OPENING assistant turn.
    Mutates in place (does not flush)."""
    brief.messages = [
        {
            "role": "assistant",
            "content": opening_message(template),
            "attachments": [],
            "suggested_replies": _select_options(_first_required_field(template)),
        }
    ]


# --------------------------------------------------------------------------- #
# The orchestrated chat turn.
# --------------------------------------------------------------------------- #
def run_chat_turn(
    db: Session,
    brief: RoleBrief,
    *,
    message: str,
    attachments: Optional[list[ChatAttachment]] = None,
    template: Optional[dict[str, Any]] = None,
    client: Any = None,
    model: Optional[str] = None,
    feature: str = _CHAT_FEATURE,
    client_org_name: Optional[str] = None,
):
    """Run ONE chat turn end-to-end and fold the result into the brief.

    Returns the ``StructuredResult`` (``.ok`` / ``.value`` / ``.error_reason``).
    On success the brief is mutated (messages appended, fields applied,
    completeness recomputed) and flushed; the caller owns the commit.

    ``feature`` is the metering bucket (defaults to the recruiter intake chat;
    the no-login CLIENT intake passes ``requisition_client_intake``).
    ``client_org_name``, when set, switches the system prompt to the
    CLIENT-FRAMED variant (consultancy's client describing the role, no pay
    questions) — pass it together with a client-scoped ``template``.
    """
    attachments = attachments or []
    if template is None:
        template = resolve_template(_org_of(brief))
    if brief.source_kind is None:
        update_brief_fields(db, brief, source_kind="conversational")

    # 1. Append the user message (+ attachment metadata) to the transcript.
    history_before = list(brief.messages or [])
    persisted_user = build_persisted_user_message(message, attachments)
    brief.messages = history_before + [persisted_user]

    # 2. Deterministic gap engine.
    gaps = compute_gaps(brief, template)
    focus = gaps[:_FOCUS_GAP_COUNT]

    # 3. ONE metered, forced-tool-use LLM call (vision-capable).
    if client is None:
        client = get_metered_client(organization_id=brief.organization_id)
    # Use the FAST chat model (CLAUDE_CHAT_MODEL = Haiku, ~5× faster round-trip)
    # rather than resolved_claude_model — on prod the latter is the recruitment
    # agent's Sonnet (reasoning quality), which made each intake turn feel slow.
    resolved_model = (
        model
        or (settings.CLAUDE_CHAT_MODEL or "").strip()
        or settings.resolved_claude_model
    )
    # Warm-start context: the org's recent role titles (excluding this brief)
    # so the agent can prefill sensibly.
    recent_titles = recent_role_titles(
        db, brief.organization_id, exclude_brief_id=brief.id
    )
    system = build_chat_system_prompt(
        brief, template, focus, recent_titles, client_org_name=client_org_name
    )
    llm_messages = _history_for_llm(history_before)
    llm_messages.append(
        {"role": "user", "content": build_user_turn_content(message, attachments)}
    )

    result = generate_structured(
        client,
        model=resolved_model,
        system=system,
        messages=llm_messages,
        output_model=ChatCapture,
        metering=MeteringContext(
            feature=feature,
            organization_id=brief.organization_id,
            role_id=brief.role_id,
            entity_id=f"role_brief:{brief.id}",
        ),
        max_tokens=_MAX_TOKENS,
        temperature=0.3,
        use_tool_use=True,
    )

    if result.ok and result.value is not None:
        # 4. Apply captured values + recompute completeness.
        apply_capture(db, brief, result.value, template)
        # Append the assistant reply to the transcript.
        reply = (result.value.assistant_reply or "").strip()
        brief.messages = list(brief.messages) + [
            {
                "role": "assistant",
                "content": reply,
                "attachments": [],
                "suggested_replies": _resolve_suggested_replies(
                    result.value, brief, template
                ),
            }
        ]
        db.flush()
    return result


def _org_of(brief: RoleBrief):
    """Lazily load the brief's organization (for template resolution)."""
    from sqlalchemy.orm import Session as _Session

    from ..models.organization import Organization

    session = _Session.object_session(brief)
    if session is None:
        return None
    return (
        session.query(Organization)
        .filter(Organization.id == brief.organization_id)
        .first()
    )


# --------------------------------------------------------------------------- #
# AI-draft the JD's "What you'll do" responsibilities list.
# --------------------------------------------------------------------------- #
def _spec_context_for_draft(brief: RoleBrief) -> dict[str, Any]:
    """The captured spec fields the responsibilities draft is grounded in
    (title, summary, seniority, department, must_haves, preferred). Only
    non-empty values are included so the prompt stays tight."""
    fields = {
        "title": brief.title,
        "summary": brief.summary,
        "seniority": brief.seniority,
        "department": brief.department,
        "must_haves": brief.must_haves,
        "preferred": brief.preferred,
    }
    return {k: v for k, v in fields.items() if not _is_empty(v)}


def _build_responsibilities_messages(brief: RoleBrief) -> list[dict[str, Any]]:
    """The single user turn: the captured spec the draft must be grounded in."""
    spec = _spec_context_for_draft(brief)
    return [
        {
            "role": "user",
            "content": (
                "Draft the responsibilities for this role. Captured spec so far:\n"
                + json.dumps(spec, separators=(",", ":"), default=str)
            ),
        }
    ]


def draft_responsibilities(
    db: Session,
    brief: RoleBrief,
    *,
    client: Any = None,
    model: Optional[str] = None,
    feature: str = _CHAT_FEATURE,
):
    """AI-draft the JD's "What you'll do" list and store it into
    ``custom_fields.responsibilities``.

    Makes ONE metered, forced-tool-use LLM call on the FAST chat model
    (``CLAUDE_CHAT_MODEL`` = Haiku) that produces 6–10 concrete responsibility
    statements (short action phrases, verb-first) for the role from the captured
    spec (title / summary / seniority / department / must_haves / preferred).

    Returns the ``StructuredResult``. On success the drafted list is merged into
    the brief's ``custom_fields`` (other custom keys preserved) and flushed; the
    caller owns the commit. On failure the brief is left untouched.
    """
    if client is None:
        client = get_metered_client(organization_id=brief.organization_id)
    # FAST chat model (Haiku) — same rationale as run_chat_turn: the resolved
    # model is the recruitment agent's Sonnet on prod, which is overkill + slow
    # for a one-shot draft.
    resolved_model = (
        model
        or (settings.CLAUDE_CHAT_MODEL or "").strip()
        or settings.resolved_claude_model
    )
    system = (
        "You are Taali's requisition intake agent. Draft "
        f"{_RESPONSIBILITIES_MIN}–{_RESPONSIBILITIES_MAX} concrete "
        "responsibilities for this role as short action statements (start with a "
        "verb, no preamble). Ground them in the captured spec; infer sensible "
        "duties for the seniority and domain. Do not fabricate company specifics "
        "(team names, products, tools) the spec doesn't mention."
    )
    result = generate_structured(
        client,
        model=resolved_model,
        system=system,
        messages=_build_responsibilities_messages(brief),
        output_model=ResponsibilitiesDraft,
        metering=MeteringContext(
            feature=feature,
            organization_id=brief.organization_id,
            role_id=brief.role_id,
            entity_id=f"role_brief:{brief.id}",
        ),
        max_tokens=_DRAFT_MAX_TOKENS,
        temperature=0.4,
        use_tool_use=True,
    )
    if result.ok and result.value is not None:
        statements = [
            str(s).strip() for s in result.value.responsibilities if str(s).strip()
        ]
        custom = dict(brief.custom_fields or {})
        custom[_RESPONSIBILITIES_KEY] = statements
        update_brief_fields(db, brief, custom_fields=custom)
        db.flush()
    return result
