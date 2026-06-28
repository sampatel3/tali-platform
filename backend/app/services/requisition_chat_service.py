"""Conversational requisition intake — one chat turn at a time (the TURN ENGINE).

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

The pure pieces live in cohesive siblings and are re-exported here so the public
import path (``app.services.requisition_chat_service``) is unchanged:

  * ``requisition_chat_capture`` — the capture schema, gap engine, completeness,
    value coercion/apply, and the deterministic single-answer path.
  * ``requisition_chat_prompt`` — attachment handling + LLM input/system-prompt
    assembly.
  * ``requisition_chat_warm_start`` — recency-biased prefill + the opening turn.

This module keeps the two LLM-calling orchestrators (``run_chat_turn`` and
``draft_responsibilities``) plus ``generate_structured`` / ``get_metered_client``
as module-level names, so the metered Anthropic call is billed + logged and the
existing monkeypatch contract (patch ``…requisition_chat_service.generate_structured``)
keeps working.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..llm.core import MeteringContext
from ..llm.structured import generate_structured
from ..models.role_brief import RoleBrief
from ..platform.config import settings
from .claude_client_resolver import get_metered_client
from .requisition_template_service import resolve_template
from .role_brief_service import update_brief_fields

# Re-exported deterministic pieces — the public API of this module is composed
# from the cohesive siblings (the import path stays stable for every caller).
from .requisition_chat_capture import (  # noqa: F401
    ChatCapture,
    _is_empty,
    _resolve_suggested_replies,
    apply_capture,
    compute_completeness,
    compute_gaps,
    next_gap_prompt,
    opening_message,
    record_answer,
)
from .requisition_chat_prompt import (  # noqa: F401
    ChatAttachment,
    _captured_brief_values,
    _history_for_llm,
    build_chat_system_prompt,
    build_persisted_user_message,
    build_user_turn_content,
)
from .requisition_chat_warm_start import (  # noqa: F401
    recent_role_titles,
    seed_opening_message,
    warm_start_fields,
    warm_start_from_roles,
)

_CHAT_FEATURE = "requisition_intake_chat"
_MAX_TOKENS = 4000
_FOCUS_GAP_COUNT = 3

# AI-draft "What you'll do" — how many concrete responsibility statements we ask
# for, and the custom_fields key they land in (no RoleBrief column → custom).
_RESPONSIBILITIES_KEY = "responsibilities"
_RESPONSIBILITIES_MIN = 6
_RESPONSIBILITIES_MAX = 10
_DRAFT_MAX_TOKENS = 1200

# The public surface — every name a caller/test imports from this module path.
# Listing them keeps the re-exports explicit (and marks them "used" for linters).
__all__ = [
    # capture
    "ChatCapture",
    "apply_capture",
    "compute_completeness",
    "compute_gaps",
    "next_gap_prompt",
    "opening_message",
    "record_answer",
    "_resolve_suggested_replies",
    # prompt
    "ChatAttachment",
    "build_chat_system_prompt",
    "build_persisted_user_message",
    "build_user_turn_content",
    "_captured_brief_values",
    # warm-start
    "recent_role_titles",
    "seed_opening_message",
    "warm_start_fields",
    "warm_start_from_roles",
    # turn engine (this module)
    "ResponsibilitiesDraft",
    "run_chat_turn",
    "draft_responsibilities",
]


class ResponsibilitiesDraft(BaseModel):
    """The AI-drafted "What you'll do" list: 6–10 concrete responsibility
    statements, each a short action phrase starting with a verb."""

    responsibilities: list[str]


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
    # Requirements GUIDANCE: hand the agent the most similar prior role's
    # requirements as a reference so its questions are sharper (captured live,
    # never auto-filled). Recruiter-side only — the no-login CLIENT intake must
    # never see the consultancy's other roles (client_org_name set => skip).
    requirements_guidance = None
    if client_org_name is None:
        from .requisition_similar_service import similar_requirements_guidance

        requirements_guidance = similar_requirements_guidance(
            db, organization_id=brief.organization_id, brief=brief
        )
    system = build_chat_system_prompt(
        brief,
        template,
        focus,
        recent_titles,
        client_org_name=client_org_name,
        requirements_guidance=requirements_guidance,
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
