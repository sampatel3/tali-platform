"""Requisition intake — ATTACHMENT handling + LLM input assembly (no DB, no LLM).

The pure pieces that turn a recruiter's turn (text + uploaded files) and the
captured brief into the inputs the metered chat call consumes:

  * ``ChatAttachment`` + kind/decoding helpers — what the route hands us
    (decoupled from FastAPI's ``UploadFile`` so this stays unit-testable).
  * ``build_persisted_user_message`` — the user turn stored on ``brief.messages``.
  * ``build_user_turn_content`` — the NEW user turn sent to the model: images
    become base64 image blocks (vision); transcripts/PDFs decode inline.
  * ``build_chat_system_prompt`` — the system prompt (template + captured-so-far
    + focus gaps + warm-start context), recruiter- or client-framed.

Split out of ``requisition_chat_service`` (the turn engine), which re-exports
these names so the public import path is unchanged.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

from pydantic import BaseModel

from ..models.role_brief import RoleBrief
from .requisition_chat_capture import _brief_value_for_field, _is_empty
from .requisition_template_service import iter_fields

logger = logging.getLogger("taali.requisition_chat")

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
    requirements_guidance: Optional[dict[str, Any]] = None,
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
    # Requirements GUIDANCE: a similar prior role's requirements as a REFERENCE
    # for the agent's questions — never auto-filled, always confirmed with the
    # user. Role-specific requirements are gathered live, not copied.
    guidance_line = ""
    if requirements_guidance:
        g = requirements_guidance
        parts = []
        if g.get("must_haves"):
            parts.append("must-haves: " + "; ".join(str(x) for x in g["must_haves"][:8]))
        if g.get("preferred"):
            parts.append("nice-to-haves: " + "; ".join(str(x) for x in g["preferred"][:6]))
        if g.get("dealbreakers"):
            parts.append("dealbreakers: " + "; ".join(str(x) for x in g["dealbreakers"][:5]))
        if parts:
            applicants = g.get("applicants") or 0
            ref = f" ({applicants} applicants)" if applicants else ""
            guidance_line = (
                f"\n\nREFERENCE ONLY — your most similar prior role, "
                f"\"{g.get('role_name', '')}\"{ref}, was hired on:\n- "
                + "\n- ".join(parts)
                + "\nUse this to ask SHARPER requirement questions (e.g. \"is the tech "
                "stack similar — still Python/Spark, or has it changed?\"). Do NOT "
                "assume or pre-fill it; confirm each point with the user and capture "
                "what THEY actually say for THIS role."
            )
    org = (client_org_name or "").strip()
    if org:
        # CLIENT-framed AND anonymous: the speaker is a client / hiring manager
        # describing a role they want filled. For safety/privacy the prompt
        # NEVER names the consultancy or any company, and NEVER asks about pay
        # (the consultancy owns economics). ``client_org_name`` is only the
        # on-switch here — its value is intentionally not rendered.
        intro = (
            "You are a requisition intake agent helping someone describe a role "
            "they want to hire for. Capture the role and its requirements — do "
            "not name or reference any company. Here is the spec template you "
            "must fill: "
        )
        comp_instruction = (
            "Do NOT ask about salary, compensation, or budget — the hiring team "
            "handles that; never raise pay even if prompted. "
        )
        closing = (
            "ALWAYS keep momentum: every reply asks the next most useful "
            "question, or — once the role is captured — thanks them and says the "
            "team will take it from here. "
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
    # Free-text-first nudge: on the user's FIRST substantive turn, absorb their
    # own-words brief and ask one sharp follow-up — don't fall back to a menu.
    user_turns = sum(
        1
        for m in (brief.messages or [])
        if isinstance(m, dict) and m.get("role") == "user"
    )
    early_line = (
        "The user has just given their first free-text brief — absorb it fully, "
        "capture every grounded detail, and ask ONE sharp follow-up rather than a "
        "menu of generic options. "
        if user_turns <= 1
        else ""
    )
    return (
        intro
        + json.dumps(compact_template, separators=(",", ":"))
        + "\n\nCaptured so far: "
        + json.dumps(captured, separators=(",", ":"), default=str)
        + "\n\nMost important gaps to close next:\n"
        + focus_lines
        + recent_line
        + guidance_line
        + "\n\nGROUND EVERYTHING IN WHAT THEY SAY. From their message and any "
        "attached transcript / screenshot, capture every field they've actually "
        "given — typed fields for standard columns, the 'custom' object for any "
        "other template key (e.g. 'urgency', 'domain'); never skip a field just "
        "because it isn't a typed column. But do NOT invent: never fabricate "
        "responsibilities, a success profile, or requirements from the job title "
        "alone. If a rich field isn't grounded in what they've told you, leave it "
        "empty and ASK; if you want to suggest content, offer it as a short DRAFT "
        "for them to confirm or edit — never record guesses as captured fact. "
        + comp_instruction
        + "DOMAIN FIRST: pin down the domain / industry early (it's required) and "
        "let it shape everything — the requirements you probe and the options you "
        "offer must fit it (e.g. in banking: regulatory compliance, data residency "
        "/ PII, model-risk governance, explainability, on-prem or no-external-LLM "
        "constraints). "
        + "Go BEYOND the basics — a strong spec is more than a title and a "
        "must-have list. Once they've described the role in their own words, probe "
        "the specifics: the TECH STACK / tools, the PROJECTS this hire will own, "
        "the CHALLENGES a great hire solves, and what GREAT looks like in 6 months "
        "— folding what they CONFIRM into must-haves / responsibilities / success "
        "profile. Don't treat the role as done until these are covered. "
        + early_line
        + "Reply conversationally — warm, concise, fast — acknowledge the "
        "specifics they gave, then ask ONE question. A SINGLE question per turn "
        "(never bundle two different things into one turn). "
        + closing
        + "QUICK REPLIES are a REFINEMENT aid, not the main input. When you're "
        "asking the user to describe something in their OWN words (their opening "
        "brief, or any open-ended 'tell me about…' question), set suggested_replies "
        "to an EMPTY list so they type or dictate. Otherwise offer up to 6 short "
        "options GROUNDED in what they've already said and the domain (never a "
        "generic menu), and every option must answer the SINGLE question you just "
        "asked — never mix fields (don't put '1 opening' next to 'Research' next "
        "to 'High urgency'). Use template options verbatim for select fields; "
        "offer sensible values for numbers / dates. Set suggested_multi to true "
        "ONLY when the question takes several answers at once (must-haves, tech "
        "stack, responsibilities, focus areas); false for single-choice "
        "(seniority, openings, urgency, one-line summary)."
    )


def _question_for_gap(template: dict[str, Any], field_key: str) -> str:
    for _s, field in iter_fields(template):
        if field.get("key") == field_key:
            return (field.get("question") or field.get("label") or field_key)
    return field_key
