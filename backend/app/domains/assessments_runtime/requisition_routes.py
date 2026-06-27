"""Requisition API (recruiter, JWT) — drive the AI-native conversational intake.

Create a requisition (which seeds an opening assistant message), then *talk* to
the intake agent (``POST /requisitions/{id}/chat``, multipart so the recruiter
can attach a kickoff-call transcript or a screenshot) — it captures the full
hiring spec against the org's requisition template. Review/edit, then publish
(materialize to a role).

Keeps the legacy single-shot ``POST /requisitions/{id}/intake`` for back-compat.
The org's spec template is read/written via ``/settings/requisition-template``.
"""
from __future__ import annotations

import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.client import Client
from ...models.organization import Organization
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.requisition_chat_service import (
    ChatAttachment,
    draft_responsibilities,
    run_chat_turn,
    seed_opening_message,
    warm_start_fields,
)
from ...services.requisition_intake_agent import run_intake_extraction
from ...services.requisition_template_service import (
    get_template_for_org,
    resolve_template,
    set_template_for_org,
)
from ...services.role_brief_service import (
    create_brief,
    publish_job_page,
    submit_brief,
    update_brief_fields,
)
# Brief serialization + the public-URL helpers live in a sibling module to keep
# this route file under the file-size gate.
from .requisition_serialization import (
    _client_intake_url,
    _job_page_url,
    _serialize_brief,
)

router = APIRouter(tags=["Requisitions"])

# Multipart upload guards for the chat endpoint.
_MAX_CHAT_FILES = 6
_MAX_CHAT_FILE_BYTES = 15 * 1024 * 1024  # 15 MB per file


def _get_brief(db: Session, organization_id: int, brief_id: int) -> RoleBrief:
    brief = (
        db.query(RoleBrief)
        .filter(RoleBrief.id == brief_id, RoleBrief.organization_id == organization_id)
        .first()
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return brief


def _org(db: Session, organization_id: int) -> Optional[Organization]:
    return (
        db.query(Organization).filter(Organization.id == organization_id).first()
    )


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class CreateRequisition(BaseModel):
    source_kind: Optional[str] = None


class IntakeInput(BaseModel):
    input: str
    source_kind: Optional[str] = None


class TemplatePut(BaseModel):
    template: dict[str, Any]


class PublishRequisition(BaseModel):
    jd_markdown: str = ""


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


@router.post("/requisitions/{brief_id}/publish")
def publish_requisition(
    brief_id: int,
    data: PublishRequisition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Publish the brief as a shareable PUBLIC job page.

    Takes the FE-rendered ``jd_markdown`` and snapshots the brief's public-safe
    fields onto a JobPage (idempotent — one per brief; re-publish refreshes it
    and reuses the token). Does NOT materialize an internal role and does NOT
    change the brief's status, so the brief stays editable for a re-publish.
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    page = publish_job_page(db, brief, jd_markdown=data.jd_markdown)
    db.commit()
    db.refresh(page)
    return {
        "job_page_id": page.id,
        "token": page.token,
        "url": _job_page_url(page.token),
        "status": page.status,
        "published_at": page.published_at.isoformat() if page.published_at else None,
    }


@router.post("/requisitions/{brief_id}/client-link")
def mint_client_link(
    brief_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mint (or return) the SCOPED, no-login CLIENT INTAKE share link.

    For a consultancy: the recruiter sends this link to their CLIENT, who
    describes the role via the same conversational agent (company/economics
    layers hidden, no pay questions). Idempotent — the token is minted once
    (``secrets.token_urlsafe(8)``) and reused on subsequent calls so a shared
    link never goes stale.
    """
    brief = _get_brief(db, current_user.organization_id, brief_id)
    if not brief.client_intake_token:
        brief.client_intake_token = secrets.token_urlsafe(8)
        db.add(brief)
        db.commit()
        db.refresh(brief)
    token = brief.client_intake_token
    return {"token": token, "url": _client_intake_url(token)}


# --------------------------------------------------------------------------- #
# Settings: the org's requisition spec template
# --------------------------------------------------------------------------- #
@router.get("/settings/requisition-template")
def get_requisition_template(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The org's requisition spec template (its override, else the built-in
    default)."""
    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"template": get_template_for_org(org)}


@router.put("/settings/requisition-template")
def put_requisition_template(
    data: TemplatePut,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate + save the org's requisition spec template."""
    org = _org(db, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    saved = set_template_for_org(db, org, data.template)
    db.commit()
    return {"template": saved}
