"""Requisition API (recruiter, JWT) — drive the AI-native conversational intake.

Create a requisition, capture the hiring spec through multipart chat, review or
edit the structured brief, then publish it into a role.

Keeps the legacy single-shot ``POST /requisitions/{id}/intake`` for back-compat.
The org's spec template is read/written via ``/settings/requisition-template``.

Core CRUD/chat lives here; publish, client-link, and template settings are
composed from sibling routers, with shared lookups in ``requisition_shared``.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.client import Client
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.requisition_chat_service import (
    ChatAttachment,
    compute_completeness,
    draft_responsibilities,
    next_gap_prompt,
    record_answer,
    run_chat_turn,
)
from ...services.requisition_intake_agent import run_intake_extraction
from ...services.requisition_template_service import resolve_template
from ...services.related_role_spec_hydration import hydrate_related_role_draft_from_saved_spec
from ...services.role_brief_service import (
    submit_brief,
    update_brief_fields,
)
from .requisition_client_link_routes import router as _client_link_router
from .requisition_publish_routes import router as _publish_router
from .requisition_route_support import (
    AnswerRequisition,
    CreateRequisition,
    IntakeInput,
    apply_manual_spec_state as _apply_manual_spec_state,
    apply_provider_changes_at_commit as _apply_provider_changes_at_commit,
    authorize_brief_mutation as _authorize_brief_mutation,
    clone_brief_for_provider_call as _clone_brief_for_provider_call,
    field_label as _field_label,
    finalize_brief_mutation as _finalize_brief_mutation,
    readable_value as _readable_value,
    start_related_role_requisition as _start_related_role_requisition,
    start_standard_requisition as _start_standard_requisition,
)
from .requisition_settings_routes import router as _settings_router
from .requisition_shared import _get_brief, _org, _serialize_brief

router = APIRouter(tags=["Requisitions"])

logger = logging.getLogger(__name__)

# Multipart upload guards for the chat endpoint.
_MAX_CHAT_FILES = 6
_MAX_CHAT_FILE_BYTES = 15 * 1024 * 1024  # 15 MB per file


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
    org = _org(db, current_user.organization_id)
    template = resolve_template(org)
    if data.source_role_id is not None:
        return _serialize_brief(
            _start_related_role_requisition(
                db,
                current_user=current_user,
                source_role_id=int(data.source_role_id),
                template=template,
            ),
            org,
        )

    return _serialize_brief(
        _start_standard_requisition(
            db,
            current_user=current_user,
            source_kind=data.source_kind,
            template=template,
        ),
        org,
    )


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
    return [_serialize_brief(b, org, include_related_preview=False) for b in briefs]


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
    expected_version: int | None = Form(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run ONE conversational intake turn. Multipart: ``message`` (may be empty
    if only files are attached) + ``files`` (transcripts / screenshots / PDFs).
    The agent captures field values against the org template and replies."""
    brief = _get_brief(db, current_user.organization_id, brief_id)
    _authorize_brief_mutation(
        db,
        brief=brief,
        current_user=current_user,
        expected_version=expected_version,
        lock_for_update=False,
    )
    baseline = _clone_brief_for_provider_call(brief)
    working_brief = _clone_brief_for_provider_call(brief)
    # Authorized compatibility recovery for related-role drafts created before
    # the cloned JD was persisted as chat source. Any resulting fields are part
    # of the normal provider delta and are re-authorized under lock at commit.
    source_pre_hydrated = hydrate_related_role_draft_from_saved_spec(working_brief)

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
        working_brief,
        message=message,
        attachments=attachments,
        template=template,
        source_pre_hydrated=source_pre_hydrated,
    )
    if not result.ok:
        db.rollback()
        logger.error("Intake chat failed: %s", result.error_reason)
        raise HTTPException(
            status_code=502, detail="The intake assistant hit a problem. Please try again."
        )
    # Re-read, re-authorize, and compare under Role -> RoleBrief locks before
    # copying the unlocked provider result onto the live row.
    brief = _apply_provider_changes_at_commit(
        db,
        baseline=baseline,
        working=working_brief,
        current_user=current_user,
        expected_version=expected_version,
        reason="requisition chat updated the linked brief",
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
    authorization = _authorize_brief_mutation(
        db,
        brief=brief,
        current_user=current_user,
        expected_version=data.expected_version,
    )
    brief = authorization.brief
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

    _finalize_brief_mutation(
        db,
        authorization=authorization,
        current_user=current_user,
        reason="requisition answer updated the linked brief",
    )
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
    _authorize_brief_mutation(
        db,
        brief=brief,
        current_user=current_user,
        expected_version=data.expected_version,
        lock_for_update=False,
    )
    baseline = _clone_brief_for_provider_call(brief)
    working_brief = _clone_brief_for_provider_call(brief)
    result = run_intake_extraction(
        db,
        working_brief,
        data.input,
        source_kind=data.source_kind,
    )
    if not result.ok:
        db.rollback()
        logger.error("Intake extraction failed: %s", result.error_reason)
        raise HTTPException(
            status_code=502, detail="The intake assistant hit a problem. Please try again."
        )
    org = _org(db, current_user.organization_id)
    working_brief.completeness = compute_completeness(
        working_brief, resolve_template(org)
    )
    brief = _apply_provider_changes_at_commit(
        db,
        baseline=baseline,
        working=working_brief,
        current_user=current_user,
        expected_version=data.expected_version,
        reason="requisition intake extraction updated the linked brief",
    )
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, org)


@router.post("/requisitions/{brief_id}/draft-responsibilities")
def draft_requisition_responsibilities(
    brief_id: int,
    expected_version: int | None = Query(default=None, ge=1),
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
    _authorize_brief_mutation(
        db,
        brief=brief,
        current_user=current_user,
        expected_version=expected_version,
        lock_for_update=False,
    )
    baseline = _clone_brief_for_provider_call(brief)
    working_brief = _clone_brief_for_provider_call(brief)
    org = _org(db, current_user.organization_id)
    result = draft_responsibilities(
        db,
        working_brief,
        template=resolve_template(org),
    )
    if not result.ok:
        db.rollback()
        logger.error("Responsibilities draft failed: %s", result.error_reason)
        raise HTTPException(
            status_code=502,
            detail="Drafting responsibilities hit a problem. Please try again.",
        )
    brief = _apply_provider_changes_at_commit(
        db,
        baseline=baseline,
        working=working_brief,
        current_user=current_user,
        expected_version=expected_version,
        reason="responsibilities draft updated the linked requisition",
    )
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, org)


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
    expected_version = data.pop("expected_version", None)
    if expected_version is not None:
        try:
            expected_version = int(expected_version)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="expected_version must be an integer"
            ) from exc
        if expected_version < 1:
            raise HTTPException(
                status_code=422, detail="expected_version must be at least 1"
            )
    authorization = _authorize_brief_mutation(
        db,
        brief=brief,
        current_user=current_user,
        expected_version=expected_version,
    )
    brief = authorization.brief
    org = _org(db, current_user.organization_id)
    template = resolve_template(org)
    _apply_manual_spec_state(brief, data, template)
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
    brief.completeness = compute_completeness(brief, template)
    _finalize_brief_mutation(
        db,
        authorization=authorization,
        current_user=current_user,
        reason="recruiter edited the linked requisition",
    )
    db.commit()
    db.refresh(brief)
    return _serialize_brief(brief, org)


@router.post("/requisitions/{brief_id}/submit")
def submit_requisition(
    brief_id: int,
    expected_version: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brief = _get_brief(db, current_user.organization_id, brief_id)
    authorization = _authorize_brief_mutation(
        db,
        brief=brief,
        current_user=current_user,
        expected_version=expected_version,
    )
    brief = authorization.brief
    submit_brief(db, brief)
    _finalize_brief_mutation(
        db,
        authorization=authorization,
        current_user=current_user,
        reason="requisition submitted for review",
    )
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
