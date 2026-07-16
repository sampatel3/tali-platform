"""Metered chat orchestrators; deterministic helpers are re-exported here."""
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
from .requisition_chat_grounding import ground_assistant_reply as _ground_assistant_reply
from .requisition_chat_source import (
    mark_source_hydrated as _mark_source_hydrated,
    persist_source_material as _persist_source_material,
    source_material_for_transcript as _source_material_for_transcript,
    source_needs_hydration as _source_needs_hydration,
)
from .requisition_template_service import resolve_template
from .role_brief_service import update_brief_fields

from .requisition_chat_capture import (  # noqa: F401
    BriefFieldChange,
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
    attachment_content_has_warning,
    build_chat_system_prompt,
    build_persisted_user_message,
    build_recoverable_source_material,
    build_user_turn_content,
    prepare_user_turn_content,
)
from .requisition_chat_warm_start import (  # noqa: F401
    recent_role_titles,
    seed_opening_message,
    warm_start_fields,
    warm_start_from_roles,
)

_CHAT_FEATURE = "requisition_intake_chat"
_MAX_TOKENS = 6000
_FOCUS_GAP_COUNT = 3
_RESPONSIBILITIES_KEY = "responsibilities"
_RESPONSIBILITIES_MIN = 6
_RESPONSIBILITIES_MAX = 10
_DRAFT_MAX_TOKENS = 1200

__all__ = [
    "BriefFieldChange",
    "ChatCapture",
    "apply_capture",
    "compute_completeness",
    "compute_gaps",
    "next_gap_prompt",
    "opening_message",
    "record_answer",
    "_resolve_suggested_replies",
    "ChatAttachment",
    "build_chat_system_prompt",
    "build_persisted_user_message",
    "build_recoverable_source_material",
    "build_user_turn_content",
    "prepare_user_turn_content",
    "_captured_brief_values",
    "recent_role_titles",
    "seed_opening_message",
    "warm_start_fields",
    "warm_start_from_roles",
    "ResponsibilitiesDraft",
    "run_chat_turn",
    "draft_responsibilities",
]


class ResponsibilitiesDraft(BaseModel):
    """The AI-drafted "What you'll do" list: 6–10 concrete responsibility
    statements, each a short action phrase starting with a verb."""

    responsibilities: list[str]


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
    transcript_attr: str = "messages",
    source_pre_hydrated: bool = False,
):
    """Run one intake turn, isolating the selected transcript; caller commits."""
    attachments = attachments or []
    if template is None:
        template = resolve_template(_org_of(brief))
    if brief.source_kind is None:
        update_brief_fields(db, brief, source_kind="conversational")

    turn_content, new_source_material = prepare_user_turn_content(message, attachments)
    _persist_source_material(
        db,
        brief,
        new_source_material,
        transcript_attr=transcript_attr,
    )
    source_material = _source_material_for_transcript(brief, transcript_attr)
    has_image_content = isinstance(turn_content, list) and any(
        isinstance(block, dict) and block.get("type") == "image"
        for block in turn_content
    )
    readable_attachment = bool(new_source_material) or has_image_content
    attachment_error = bool(attachments) and not readable_attachment
    attachment_warning = bool(attachments) and attachment_content_has_warning(
        turn_content
    )
    document_turn = readable_attachment or _source_needs_hydration(
        brief,
        source_material,
        transcript_attr,
    )

    history_before = list(getattr(brief, transcript_attr, None) or [])
    persisted_user = build_persisted_user_message(message, attachments)
    setattr(brief, transcript_attr, history_before + [persisted_user])

    gaps = compute_gaps(brief, template)
    focus = gaps if document_turn else gaps[:_FOCUS_GAP_COUNT]

    if client is None:
        client = get_metered_client(organization_id=brief.organization_id)
    state = dict(brief.agent_state or {})
    intent_sensitive = bool(
        brief.source_role_id
        or state.get("jd_override")
        or state.get("pending_job_spec_source")
        or any(
            isinstance(item, dict) and item.get("role") == "user"
            for item in history_before
        )
    )
    resolved_model = (
        model
        or (
            settings.resolved_claude_model
            if intent_sensitive
            else (settings.CLAUDE_CHAT_MODEL or "").strip()
        )
        or settings.resolved_claude_model
    )
    recent_titles = recent_role_titles(
        db, brief.organization_id, exclude_brief_id=brief.id
    )
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
        transcript=getattr(brief, transcript_attr, None),
        source_material=source_material,
        document_turn=document_turn,
    )
    captured_before = _captured_brief_values(brief, template)
    llm_messages = _history_for_llm(history_before)
    llm_messages.append({"role": "user", "content": turn_content})

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
        change_mode = str(result.value.change_mode or "amend")
        if change_mode == "clarify" and new_source_material and not result.value.pending_job_spec:
            result.value.pending_job_spec = new_source_material
        if change_mode == "clarify" and not result.value.suggested_replies:
            result.value.suggested_replies = [
                "Replace current draft",
                "Apply differences only",
            ]
        apply_capture(
            db,
            brief,
            result.value,
            template,
            transcript_attr=transcript_attr,
        )
        captured_after = _captured_brief_values(brief, template)
        post_capture_gaps = compute_gaps(brief, template)
        capture_changed = captured_after != captured_before
        canonical_supplied = bool(str(result.value.canonical_job_spec or "").strip())
        changed_keys = sorted(set(captured_before) | set(captured_after), key=str)
        changed_keys = [
            key for key in changed_keys if captured_before.get(key) != captured_after.get(key)
        ]
        if change_mode != "clarify" and (
            capture_changed or canonical_supplied or not post_capture_gaps
        ):
            _mark_source_hydrated(
                db,
                brief,
                _source_material_for_transcript(brief, transcript_attr),
                transcript_attr,
            )
        reply, overridden = _ground_assistant_reply(
            brief=brief,
            template=template,
            message=message,
            model_reply=result.value.assistant_reply,
            document_turn=document_turn,
            attachment_error=attachment_error,
            attachment_warning=attachment_warning,
            source_updated=source_pre_hydrated or capture_changed or canonical_supplied,
            change_mode=change_mode,
            changed_keys=changed_keys,
            client_org_name=client_org_name,
        )
        result.value.assistant_reply = reply
        if overridden:
            result.value.suggested_replies = []
            result.value.suggested_multi = False
        setattr(brief, transcript_attr, list(getattr(brief, transcript_attr) or []) + [
            {
                "role": "assistant",
                "content": reply,
                "attachments": [],
                "suggested_replies": _resolve_suggested_replies(
                    result.value, brief, template
                ),
                "suggested_multi": bool(getattr(result.value, "suggested_multi", False)),
                "change_mode": change_mode,
                "changed_fields": changed_keys,
            }
        ])
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
    template: Optional[dict[str, Any]] = None,
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
        state = dict(brief.agent_state or {})
        if state.get("jd_override"):
            state.pop("jd_override", None)
            state["canonical_spec_mode"] = "structured"
            try:
                revision = int(state.get("job_spec_revision") or 0) + 1
            except (TypeError, ValueError):
                revision = 1
            state["job_spec_revision"] = revision
            state["job_spec_last_change_mode"] = "draft_responsibilities"
            state.pop("pending_job_spec_source", None)
            update_brief_fields(db, brief, agent_state=state)
        resolved_template = template or resolve_template(_org_of(brief))
        brief.completeness = compute_completeness(brief, resolved_template)
        db.flush()
    return result


class CompanyBlurbDraft(BaseModel):
    """The role-agnostic 'About the company' description extracted from specs."""

    company_description: str = ""


_COMPANY_BLURB_FEATURE = "requisition_intake"
_COMPANY_BLURB_SOURCE_ROLES = 3
_COMPANY_BLURB_MAX_TOKENS = 400


def derive_company_blurb(
    db: Session,
    organization_id: int,
    *,
    client: Any = None,
    model: Optional[str] = None,
) -> Optional[str]:
    """The org's standardised "About the company" blurb, derived ONCE from recent
    role specs and cached on ``organizations.company_blurb``.

    The company description recurs verbatim across an org's job specs but, in
    per-role JD bodies, sits tangled with role-specific intro text — so a single
    cheap LLM extraction (FAST model, metered) pulls out the reusable, role-free
    company description. Cached so it runs once per org (``""`` caches a
    no-result so we don't re-call). Best-effort: any failure returns None without
    caching (so a later create retries). Never raises."""
    from ..models.organization import Organization
    from ..models.role import Role

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        return None
    cached = getattr(org, "company_blurb", None)
    if cached is not None:
        return cached or None

    roles = (
        db.query(Role)
        .filter(
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
            Role.job_spec_text.isnot(None),
        )
        .order_by(Role.created_at.desc(), Role.id.desc())
        .limit(_COMPANY_BLURB_SOURCE_ROLES * 4)
        .all()
    )
    specs: list[str] = []
    for role in roles:
        text = (role.job_spec_text or "").strip()
        if text:
            specs.append(text[:4000])
        if len(specs) >= _COMPANY_BLURB_SOURCE_ROLES:
            break
    if not specs:
        org.company_blurb = ""  # nothing to derive from yet — cache the no-result
        db.flush()
        return None

    if client is None:
        client = get_metered_client(organization_id=organization_id)
    resolved_model = (
        model or (settings.CLAUDE_CHAT_MODEL or "").strip() or settings.resolved_claude_model
    )
    system = (
        "Extract ONLY the standardised 'About the company' description from these "
        "job specs — who the employer is and what it does. STRIP everything "
        "role-specific (the role itself, responsibilities, requirements, benefits, "
        "location, pay). Return a concise, reusable 1–3 sentence company "
        "description that would fit on ANY of this company's job posts. If the "
        "specs contain no clear company description, return an empty string."
    )
    messages = [
        {"role": "user", "content": "Job specs:\n\n" + "\n\n---\n\n".join(specs)}
    ]
    try:
        result = generate_structured(
            client,
            model=resolved_model,
            system=system,
            messages=messages,
            output_model=CompanyBlurbDraft,
            metering=MeteringContext(
                feature=_COMPANY_BLURB_FEATURE,
                organization_id=organization_id,
                entity_id=f"org:{organization_id}:company_blurb",
            ),
            max_tokens=_COMPANY_BLURB_MAX_TOKENS,
            temperature=0.2,
            use_tool_use=True,
        )
    except Exception:
        return None  # transient — don't cache, retry on a later create

    blurb = ""
    if result.ok and result.value is not None:
        blurb = (result.value.company_description or "").strip()
    org.company_blurb = blurb  # cache the result (incl. "" = derived, none found)
    db.flush()
    return blurb or None
