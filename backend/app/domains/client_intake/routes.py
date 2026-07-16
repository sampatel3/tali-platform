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

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...deps import get_optional_current_user
from ...models.organization import Organization
from ...models.role_brief import RoleBrief
from ...models.user import User
from ...platform.database import get_db
from ...services.pricing_service import Feature
from ...services.requisition_chat_service import (
    _captured_brief_values,
    compute_completeness,
    compute_gaps,
    opening_message,
    run_chat_turn,
)
from ...services.requisition_chat_uploads import read_requisition_chat_attachments
from ...services.requisition_template_service import (
    client_scoped_template,
    resolve_template,
)

public_router = APIRouter(prefix="/api/v1/public", tags=["Client intake"])

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


def _require_open_intake(brief: RoleBrief) -> None:
    # Publication/materialization freezes this source brief. The live Role is
    # now authoritative and protected by its hiring team + revision contract;
    # an old public token must not remain a second, unauthenticated editor.
    if brief.role_id is not None or str(brief.status or "").lower() == "applied":
        raise HTTPException(
            status_code=409,
            detail="This intake is closed because the requisition was published.",
        )


def _ensure_client_opening(
    db: Session, brief: RoleBrief, client_template: dict[str, Any]
) -> list:
    """Seed the hiring-manager transcript (``client_messages``) with its OWN
    opening turn if empty, so the manager starts a fresh free-text-first
    conversation and NEVER sees the recruiter's ``messages`` (which may hold
    confidential internal context). Idempotent; flushes. Returns the transcript."""
    if not (brief.client_messages or []):
        brief.client_messages = [
            {
                "role": "assistant",
                "content": opening_message(client_template),
                "attachments": [],
                "suggested_replies": [],
            }
        ]
        db.flush()
    return brief.client_messages or []


def _user_turn_count(brief: RoleBrief) -> int:
    # Count the HIRING-MANAGER transcript only — the recruiter's turns must not
    # eat the public intake's anti-abuse cap.
    return sum(
        1
        for m in (brief.client_messages or [])
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
    # The hiring manager sees their OWN transcript (seeded with a fresh opener on
    # first view), never the recruiter's ``messages``.
    needs_seed = not (brief.client_messages or []) and brief.role_id is None
    client_messages = (
        _ensure_client_opening(db, brief, client_template)
        if brief.role_id is None
        else (
            brief.client_messages
            or [
                {
                    "role": "assistant",
                    "content": opening_message(client_template),
                    "attachments": [],
                    "suggested_replies": [],
                }
            ]
        )
    )
    if needs_seed:
        db.commit()
    return {
        # Intentionally NOT the real org name — the client surface stays generic.
        "organization_name": None,
        "messages": client_messages,
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
    _require_open_intake(brief)

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
    attachments = await read_requisition_chat_attachments(files)

    org = (
        db.query(Organization)
        .filter(Organization.id == brief.organization_id)
        .first()
    )
    client_template = client_scoped_template(resolve_template(org))
    # Make sure the manager's transcript has its own opener before we append.
    _ensure_client_opening(db, brief, client_template)

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
        # Read + append the HIRING-MANAGER transcript, never the recruiter's.
        transcript_attr="client_messages",
    )
    if not result.ok:
        db.rollback()
        raise HTTPException(
            status_code=502, detail=f"Intake chat failed: {result.error_reason}"
        )
    db.commit()
    db.refresh(brief)

    messages = brief.client_messages or []
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
    _require_open_intake(brief)
    if brief.status not in ("submitted", "applied"):
        brief.status = "submitted"
        db.add(brief)
        db.commit()
        db.refresh(brief)
    return {"ok": True, "status": brief.status}
