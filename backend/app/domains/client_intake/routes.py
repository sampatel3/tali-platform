"""Public, no-auth CLIENT INTAKE — the scoped share link a consultancy sends to
its CLIENT.

The recruiter mints a link (``/requisitions/{id}/client-link``); the client opens
it and describes the role they want the consultancy to hire for via the SAME
conversational intake agent — but the company/internal/economic layers are
hidden and the agent NEVER asks about salary / compensation / budget (the
consultancy owns economics).

Mirrors the public job-page router: token in the path, optional-auth (a stray
Authorization header never bounces an anonymous client), mounted at app root
under ``/api/v1/public``. Resolves the RoleBrief by its
``client_intake_token``.

Deliberately exposes ROLE-safe fields ONLY — never ``client_rate`` / ``margin``
/ ``client_id`` / ``salary_*`` / JD internals. The chat runs CLIENT-SCOPED: a
client-scoped template (compensation section removed) + a client-framed system
prompt, metered under the dedicated ``requisition_client_intake`` feature.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.organization import Organization
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.pricing_service import Feature
from ...services.requisition_chat_service import (
    ChatAttachment,
    _captured_brief_values,
    compute_completeness,
    compute_gaps,
    run_chat_turn,
)
from ...services.requisition_template_service import (
    client_scoped_template,
    resolve_template,
)

public_router = APIRouter(prefix="/api/v1/public", tags=["Client intake"])

# Multipart upload guards — reuse the recruiter chat's caps verbatim.
_MAX_CHAT_FILES = 6
_MAX_CHAT_FILE_BYTES = 15 * 1024 * 1024  # 15 MB per file

# Anti-abuse: cap total USER turns per token (assistant turns + the opening
# greeting don't count). A client describing a role rarely needs many turns;
# this stops an open link being used to burn metered Claude calls.
_MAX_USER_TURNS = 60

# The ROLE-safe field keys the public payload may expose — the role + its
# requirements + role-ish context. Anything not in this set (economics:
# client_rate/margin/client_id, pay: salary_*, JD internals) is dropped, even
# if some template put it in a client-visible section. Custom template keys
# that look role-ish (e.g. responsibilities, urgency) pass through the
# allowlisted-prefixes check below.
_ROLE_SAFE_CAPTURE_KEYS = frozenset(
    {
        "title",
        "summary",
        "seniority",
        "must_haves",
        "preferred",
        "dealbreakers",
        "success_profile",
        "assessment_focus",
        # Role context a HIRING MANAGER legitimately sets. Logistics that are
        # HR/People's call (location / workplace / employment / department) are
        # deliberately NOT here — the intake stays on the role itself.
        "responsibilities",
        "openings",
        "urgency",
    }
)
# Keys we must NEVER expose, regardless of where they sit in a template.
_FORBIDDEN_CAPTURE_KEYS = frozenset(
    {
        "client_rate",
        "margin",
        "margin_pct",
        "client_id",
        "client_name",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "bonus",
        "equity",
    }
)


def _role_safe_captured(brief: RoleBrief, client_template: dict[str, Any]) -> dict[str, Any]:
    """The brief's captured values, filtered to ROLE-safe keys only.

    Starts from the values captured against the CLIENT-scoped template (which
    already excludes the whole compensation section), then keeps only keys that
    are explicitly role-safe and never in the forbidden set — defence in depth
    so no economics / pay / client-identity field can leak even if a custom
    template misplaces one.
    """
    captured = _captured_brief_values(brief, client_template)
    return {
        k: v
        for k, v in captured.items()
        if k in _ROLE_SAFE_CAPTURE_KEYS and k not in _FORBIDDEN_CAPTURE_KEYS
    }


def _resolve_brief(db: Session, token: str) -> RoleBrief:
    brief = (
        db.query(RoleBrief).filter(RoleBrief.client_intake_token == token).first()
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="Intake link not found")
    return brief


def _user_turn_count(brief: RoleBrief) -> int:
    return sum(
        1
        for m in (brief.messages or [])
        if isinstance(m, dict) and m.get("role") == "user"
    )


@public_router.get("/intake/{token}")
def view_client_intake(
    token: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """The client's view of the scoped intake: the transcript so far + the live
    spec progress computed against the CLIENT-scoped template. ROLE-safe fields
    only, and deliberately ANONYMOUS — the consultancy/org name is never exposed
    (safety/privacy); the page reads as a generic role intake."""
    brief = _resolve_brief(db, token)
    org = (
        db.query(Organization)
        .filter(Organization.id == brief.organization_id)
        .first()
    )
    client_template = client_scoped_template(resolve_template(org))
    return {
        # Intentionally NOT the real org name — the client surface stays generic.
        "organization_name": None,
        "messages": brief.messages or [],
        "captured": _role_safe_captured(brief, client_template),
        "gaps": compute_gaps(brief, client_template),
        "completeness": compute_completeness(brief, client_template),
        "status": brief.status,
    }


@public_router.post("/intake/{token}/chat")
async def chat_client_intake(
    token: str,
    message: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """Run ONE CLIENT-SCOPED conversational turn on the same brief: the
    client-scoped template + a client-framed prompt (no pay questions),
    metered under ``requisition_client_intake``. Returns ROLE-safe progress.
    """
    brief = _resolve_brief(db, token)

    # Anti-abuse: cap total user turns on an open link.
    if _user_turn_count(brief) >= _MAX_USER_TURNS:
        raise HTTPException(
            status_code=429,
            detail="This intake link has reached its message limit.",
        )

    message = message or ""
    files = files or []
    if not message.strip() and not files:
        raise HTTPException(
            status_code=422, detail="Provide a message or at least one file"
        )
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

    org = (
        db.query(Organization)
        .filter(Organization.id == brief.organization_id)
        .first()
    )
    client_template = client_scoped_template(resolve_template(org))

    result = run_chat_turn(
        db,
        brief,
        message=message,
        attachments=attachments,
        template=client_template,
        feature=Feature.REQUISITION_CLIENT_INTAKE.value,
        # Generic, non-empty gate: switches the prompt to CLIENT-framed + no-pay
        # mode WITHOUT passing the real org name (privacy — the prompt is anon).
        client_org_name="client",
    )
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status_code=502, detail=f"Intake chat failed: {result.error_reason}"
        )
    db.commit()
    db.refresh(brief)

    messages = brief.messages or []
    last = messages[-1] if messages else {}
    return {
        "reply": (result.value.assistant_reply if result.value else "") or "",
        "messages": messages,
        "captured": _role_safe_captured(brief, client_template),
        "gaps": compute_gaps(brief, client_template),
        # The bar must update every turn — the GET snapshot is the only other
        # place this is computed, so omitting it here froze the meter at its
        # initial value (0%) no matter how much the manager filled in.
        "completeness": compute_completeness(brief, client_template),
        "suggested_replies": (
            (last.get("suggested_replies") or []) if isinstance(last, dict) else []
        ),
        "suggested_multi": (
            bool(last.get("suggested_multi")) if isinstance(last, dict) else False
        ),
    }


@public_router.post("/intake/{token}/submit")
def submit_client_intake(
    token: str,
    db: Session = Depends(get_db),
    _user: User | None = Depends(get_optional_current_user),
):
    """The client signals they're done: mark the brief submitted. Idempotent —
    an already-submitted (or applied) brief is left as-is."""
    brief = _resolve_brief(db, token)
    if brief.status not in ("submitted", "applied"):
        brief.status = "submitted"
        db.add(brief)
        db.commit()
        db.refresh(brief)
    return {"ok": True, "status": brief.status}
