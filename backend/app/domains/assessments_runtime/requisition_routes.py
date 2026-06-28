"""Requisition API (recruiter, JWT) — drive the AI-native conversational intake.

Create a requisition (which seeds an opening assistant message), then *talk* to
the intake agent (``POST /requisitions/{id}/chat``, multipart so the recruiter
can attach a kickoff-call transcript or a screenshot) — it captures the full
hiring spec against the org's requisition template. Review/edit, then publish
(materialize to a role).

Keeps the legacy single-shot ``POST /requisitions/{id}/intake`` for back-compat.
The org's spec template is read/written via ``/settings/requisition-template``.

This module owns the core CRUD + chat surface; the publish, client-link, and
template-settings surfaces live in sibling ``*_routes`` modules and are composed
back onto ``router`` via ``include_router`` (paths/prefix unchanged), with the
shared serializer + lookups in ``requisition_shared``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.client import Client
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.requisition_chat_service import (
    ChatAttachment,
    draft_responsibilities,
    next_gap_prompt,
    record_answer,
    run_chat_turn,
    seed_opening_message,
    warm_start_fields,
)
from ...services.requisition_intake_agent import run_intake_extraction
from ...services.requisition_template_service import (
    iter_fields,
    resolve_template,
)
from ...services.role_brief_service import (
    create_brief,
    submit_brief,
    update_brief_fields,
)
from .requisition_client_link_routes import router as _client_link_router
from .requisition_publish_routes import router as _publish_router
from .requisition_settings_routes import router as _settings_router
from .requisition_shared import _get_brief, _org, _serialize_brief

router = APIRouter(tags=["Requisitions"])

# Multipart upload guards for the chat endpoint.
_MAX_CHAT_FILES = 6
_MAX_CHAT_FILE_BYTES = 15 * 1024 * 1024  # 15 MB per file


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class CreateRequisition(BaseModel):
    source_kind: Optional[str] = None


class IntakeInput(BaseModel):
    input: str
    source_kind: Optional[str] = None


class AnswerRequisition(BaseModel):
    """A single structured answer to one requisition field (e.g. a tapped
    quick-reply). ``value`` is open — string, number, or list."""

    field_key: str
    value: Any = None


# --------------------------------------------------------------------------- #
# CRUD + intake
# --------------------------------------------------------------------------- #
@router.post("/requisitions", status_code=201)
def create_requisition(
    data: CreateRequisition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a requisition and seed the OPENING assistant message (greeting +
    the first required question from the org's template)."""
    brief = create_brief(
        db,
        organization_id=current_user.organization_id,
        created_by_user_id=current_user.id,
        source_kind=data.source_kind,
    )
    # Salary defaults to AED (UAE-based org) so the agent never asks currency.
    brief.salary_currency = "AED"
    org = _org(db, current_user.organization_id)
    template = resolve_template(org)
    # Warm-start: prefill location/workplace/employment/department from the org's
    # most-recent specs (the agent/recruiter can still override). These count
    # toward the live gap engine + completeness and are visible to the agent as
    # already captured. ``completeness`` itself is (re)computed on the first chat
    # turn — we don't seed it here, to keep a brand-new brief at 0% until the
    # recruiter starts talking, matching the existing create contract.
    for field, value in warm_start_fields(
        db, current_user.organization_id, exclude_brief_id=brief.id
    ).items():
        setattr(brief, field, value)
    seed_opening_message(brief, template)
    db.flush()
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, org)


@router.get("/requisitions")
def list_requisitions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = _org(db, current_user.organization_id)
    briefs = (
        db.query(RoleBrief)
        .filter(RoleBrief.organization_id == current_user.organization_id)
        .order_by(RoleBrief.id.desc())
        .all()
    )
    return [_serialize_brief(b, org) for b in briefs]


@router.get("/requisitions/{brief_id}")
def get_requisition(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brief = _get_brief(db, current_user.organization_id, brief_id)
    return _serialize_brief(brief, _org(db, current_user.organization_id))


@router.post("/requisitions/{brief_id}/chat")
async def chat_requisition(
    brief_id: int,
    message: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run ONE conversational intake turn. Multipart: ``message`` (may be empty
    if only files are attached) + ``files`` (transcripts / screenshots / PDFs).
    The agent captures field values against the org template and replies."""
    brief = _get_brief(db, current_user.organization_id, brief_id)

    message = message or ""
    files = files or []
    if not message.strip() and not files:
        raise HTTPException(status_code=422, detail="Provide a message or at least one file")
    if len(files) > _MAX_CHAT_FILES:
        raise HTTPException(
            status_code=422, detail=f"At most {_MAX_CHAT_FILES} files per turn"
        )

    attachments: list[ChatAttachment] = []
    for upload in files:
        content = await upload.read()
        if len(content) > _MAX_CHAT_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename or 'file'} exceeds the 15 MB per-file limit",
            )
        attachments.append(
            ChatAttachment(
                name=(upload.filename or "attachment"),
                content_type=upload.content_type,
                content=content,
            )
        )

    org = _org(db, current_user.organization_id)
    template = resolve_template(org)
    result = run_chat_turn(
        db,
        brief,
        message=message,
        attachments=attachments,
        template=template,
    )
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status_code=502, detail=f"Intake chat failed: {result.error_reason}"
        )
    db.commit()
    db.refresh(brief)
    payload = _serialize_brief(brief, org)
    messages = payload["messages"]
    last = messages[-1] if messages else {}
    return {
        "brief": payload,
        "reply": (result.value.assistant_reply if result.value else "") or "",
        "messages": messages,
        "gaps": payload["gaps"],
        "suggested_replies": (last.get("suggested_replies") or []) if isinstance(last, dict) else [],
    }


def _readable_value(value: Any) -> str:
    """Render an answer value as a short readable string for the transcript /
    reply (lists joined with ", ")."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return "" if value is None else str(value).strip()


def _field_label(template: dict[str, Any], field_key: str) -> str:
    """The template label for a field key (falls back to the key itself)."""
    for _section, field in iter_fields(template):
        if field.get("key") == field_key:
            return field.get("label") or field_key
    return field_key


@router.post("/requisitions/{brief_id}/answer")
def answer_requisition(
    brief_id: int,
    data: AnswerRequisition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record ONE structured answer (a tapped quick-reply or a single field
    value) WITHOUT any LLM call.

    Deterministically captures ``value`` onto ``field_key`` (coerced by the
    template field type, routed to its column or ``custom_fields``), appends the
    user answer + a deterministic assistant acknowledgement (which asks the next
    gap's question) to the transcript, and returns the same shape as ``/chat``.
    Unknown ``field_key`` → 422. No metering, no Anthropic call.
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    org = _org(db, current_user.organization_id)
    template = resolve_template(org)

    readable_value = _readable_value(data.value)
    # Append the USER answer to the transcript.
    brief.messages = list(brief.messages or []) + [
        {"role": "user", "content": readable_value, "attachments": []}
    ]

    # Deterministically record the field (raises 422 on an unknown key / empty).
    record_answer(db, brief, template, data.field_key, data.value)

    # Deterministic acknowledgement + the next gap's question/options.
    field_label = _field_label(template, data.field_key)
    reply_q, options = next_gap_prompt(template, brief)
    reply = f"Got it — {field_label}: {readable_value}. " + reply_q
    brief.messages = list(brief.messages) + [
        {
            "role": "assistant",
            "content": reply,
            "attachments": [],
            "suggested_replies": options,
        }
    ]

    db.commit()
    db.refresh(brief)
    payload = _serialize_brief(brief, org)
    return {
        "brief": payload,
        "reply": reply,
        "messages": payload["messages"],
        "gaps": payload["gaps"],
        "suggested_replies": options,
    }


@router.post("/requisitions/{brief_id}/intake")
def run_requisition_intake(
    brief_id: int,
    data: IntakeInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Legacy single-shot intake over pasted notes / transcript / JD (kept for
    back-compat). Prefer ``/chat``. Calls Claude (metered)."""
    brief = _get_brief(db, current_user.organization_id, brief_id)
    result = run_intake_extraction(db, brief, data.input, source_kind=data.source_kind)
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status_code=502, detail=f"Intake extraction failed: {result.error_reason}"
        )
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, _org(db, current_user.organization_id))


@router.post("/requisitions/{brief_id}/draft-responsibilities")
def draft_requisition_responsibilities(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI-draft the JD's "What you'll do" list from the captured spec.

    Makes ONE metered Claude call (the fast chat model) that produces 6–10
    concrete responsibility statements and stores them into
    ``custom_fields.responsibilities`` (which the ``{{responsibilities}}`` JD
    placeholder renders). Returns the full serialized brief. On LLM failure →
    rollback + 502, mirroring ``/chat`` and ``/intake``.
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    result = draft_responsibilities(db, brief)
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"Responsibilities draft failed: {result.error_reason}",
        )
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, _org(db, current_user.organization_id))


@router.patch("/requisitions/{brief_id}")
def update_requisition(
    brief_id: int,
    data: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recruiter edits to the brief (whitelisted fields, including
    ``custom_fields`` and the consultancy ``client_id`` / ``client_rate``)."""
    brief = _get_brief(db, current_user.organization_id, brief_id)
    data = dict(data or {})
    # ``jd_override`` is the recruiter's hand-edited Job spec. It isn't a column —
    # merge it into agent_state (preserving other keys like ``open_questions``);
    # an empty string / null clears it. Pull it out so it doesn't flow through
    # update_brief_fields as a column.
    if "jd_override" in data:
        raw = data.pop("jd_override")
        override = (raw or "").strip() if isinstance(raw, str) else raw
        state = dict(brief.agent_state or {})
        if override:
            state["jd_override"] = override
        else:
            state.pop("jd_override", None)
        brief.agent_state = state
    # A client_id can only point at a client in the caller's org (no cross-org
    # assignment). ``None`` clears the link.
    if data.get("client_id") is not None:
        client = (
            db.query(Client)
            .filter(
                Client.id == data["client_id"],
                Client.organization_id == current_user.organization_id,
            )
            .first()
        )
        if client is None:
            raise HTTPException(status_code=404, detail="Client not found")
    update_brief_fields(db, brief, **data)
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, _org(db, current_user.organization_id))


@router.post("/requisitions/{brief_id}/submit")
def submit_requisition(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brief = _get_brief(db, current_user.organization_id, brief_id)
    submit_brief(db, brief)
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, _org(db, current_user.organization_id))


# --------------------------------------------------------------------------- #
# Compose the split-out surfaces onto this router (paths/prefix unchanged):
# publish, client-link, and the org's requisition-template settings.
# --------------------------------------------------------------------------- #
router.include_router(_publish_router)
router.include_router(_client_link_router)
router.include_router(_settings_router)
