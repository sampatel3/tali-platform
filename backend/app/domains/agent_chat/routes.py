"""HTTP routes for the role-agent chat.

  GET  /api/v1/agent-chat/conversations                       sidebar (active agents + badges)
  GET  /api/v1/agent-chat/conversations/{role_id}/timeline    merged chat + questions + decisions
  POST /api/v1/agent-chat/conversations/{role_id}/messages    send a message → run the agent turn
  POST /api/v1/agent-chat/conversations/{role_id}/read        mark the thread read

The conversation is keyed by ``role_id`` (one shared thread per role's
agent) and created lazily on first access. Questions and decisions are
*projected* into the timeline; they're still answered / approved through the
existing ``/agent`` + ``/agent-decisions`` endpoints.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...agent_chat.draft_tasks import (
    REJECT_QUESTIONS,
    approve_draft,
    revise_draft,
)
from ...agent_chat.engine import persist_user_message
from ...agent_chat.proactive import maybe_post_helper_briefing
from ...agent_chat.service import (
    conversation_agent_working,
    ensure_conversation,
    get_owned_role,
    list_agent_conversations,
    mark_read,
    post_agent_message,
)
from ...agent_chat.timeline import build_timeline, serialize_message
from ...deps import get_current_user
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...platform.config import settings
from ...platform.database import get_db

logger = logging.getLogger("taali.agent_chat.routes")

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class BulkMessageRequest(BaseModel):
    # Explicit role ids (the recruiter's multi-selection) — no implicit
    # "all roles of type X", same deliberate choice as bulk-approve.
    role_ids: list[int] = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=8000)


class ReviseDraftRequest(BaseModel):
    # Structured reject answers keyed by question (e.g. {"issues": [...],
    # "direction": "harder"}) + an optional free-text note. Interpreted by
    # ``draft_tasks._build_feedback``.
    answers: dict = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2000)


def _require_org(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(status_code=400, detail="User has no organization")
    return int(current_user.organization_id)


def _require_role(db: Session, role_id: int, organization_id: int) -> Role:
    role = get_owned_role(db, role_id=role_id, organization_id=organization_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def _agent_meta(role: Role) -> dict:
    return {
        "enabled": bool(role.agentic_mode_enabled),
        "paused": role.agent_paused_at is not None,
        "paused_reason": role.agent_paused_reason,
        "monthly_budget_cents": role.monthly_usd_budget_cents,
        "score_threshold": role.score_threshold,
    }


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    return {"agents": list_agent_conversations(db, organization_id=org_id, user=current_user)}


@router.post("/bulk-message")
def bulk_message(
    body: BulkMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fan one message out to several roles' agents at once.

    Each selected role's agent runs the message in ITS OWN thread (separate
    turn, separate audit) via a background job that paces the turns
    sequentially per org. Validates org-ownership of every role up front and
    reports any it dropped, then enqueues — returns immediately; the replies
    land in each role's thread as the job drains.
    """
    org_id = _require_org(current_user)
    role_ids = list(dict.fromkeys(int(x) for x in body.role_ids))  # de-dupe, keep order
    owned = {
        int(r.id)
        for r in db.query(Role)
        .filter(
            Role.organization_id == org_id,
            Role.id.in_(role_ids),
            Role.deleted_at.is_(None),
        )
        .all()
    }
    accepted = [rid for rid in role_ids if rid in owned]
    if not accepted:
        raise HTTPException(status_code=400, detail="No valid roles selected")

    from ...tasks.agent_chat_tasks import bulk_agent_message

    bulk_agent_message.delay(org_id, int(current_user.id), accepted, body.message.strip())
    return {
        "requested": len(role_ids),
        "accepted": len(accepted),
        "skipped": [rid for rid in role_ids if rid not in owned],
    }


@router.get("/conversations/{role_id}/timeline")
def get_timeline(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    # Speak first on a fresh or materially changed role without paying for a
    # model turn. The deterministic helper emits at most one suggested next
    # step and never changes recruiting state.
    try:
        # Helper generation is optional. Isolate any partial helper writes in a
        # savepoint so a failure cannot poison the outer transaction that owns
        # lazy conversation creation and the normal timeline read.
        with db.begin_nested():
            maybe_post_helper_briefing(db, conversation=conversation, role=role)
    except Exception:
        logger.exception(
            "optional helper briefing failed for role_id=%s; serving timeline",
            role.id,
        )
    timeline = build_timeline(db, conversation=conversation, role=role)
    working = conversation_agent_working(db, conversation)
    # A fetch is not an acknowledgement. The clients call POST /read only
    # after the selected thread has remained visibly open for a short dwell,
    # preventing auto-selection or background reads from consuming alerts.
    db.commit()
    return {
        "conversation_id": conversation.id,
        "role_id": role.id,
        "role_name": role.name,
        "agent": _agent_meta(role),
        "timeline": timeline,
        # Recomputed from persisted state, so the "agent is working…" indicator
        # survives navigation / an agent switch and resumes on return.
        "agent_working": working,
    }


@router.post("/conversations/{role_id}/messages")
def send_message(
    role_id: int,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    organization = (
        db.query(Organization).filter(Organization.id == org_id).first()
    )
    if organization is None:
        raise HTTPException(status_code=400, detail="Organization not found")
    conversation = ensure_conversation(db, organization_id=org_id, role=role)

    # One turn at a time PER agent: reject a second message while this agent is
    # still working on the previous one, rather than starting a second turn that
    # would replay a half-finished history and double-reply. This guard is
    # per-conversation, so you can still message OTHER agents concurrently (and
    # bulk-message fans out to many agents at once) — it only serialises a single
    # agent's own thread.
    if conversation_agent_working(db, conversation):
        raise HTTPException(
            status_code=409,
            detail="The agent is still working on your previous message — it'll reply in a moment.",
        )

    # Persist the recruiter's message synchronously and commit — it's durable the
    # instant they hit send, surviving navigation / an agent switch / a failed
    # turn. The slow, mutating model loop runs in a worker (run_agent_chat_turn);
    # the reply lands in the thread and the dock polls + notifies when it does.
    try:
        user_row = persist_user_message(
            db=db, conversation=conversation, user=current_user, user_message=body.message
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # The sender has read up to their own message; the agent's reply (posted by
    # the worker, later) then counts as unread → drives the reply notification.
    mark_read(db, conversation=conversation, user=current_user)
    user_payload = serialize_message(user_row)
    db.commit()

    # Build the response BEFORE enqueuing so it's identical under eager Celery
    # (tests) and prod: the POST returns the user message + "working", and the
    # reply is observed on the next timeline read.
    timeline = build_timeline(db, conversation=conversation, role=role)
    response = {
        "conversation_id": conversation.id,
        "role_id": role.id,
        "status": "accepted",
        "agent_working": True,
        "messages": [user_payload],
        "timeline": timeline,
        "agent": _agent_meta(role),
    }

    from ...tasks.agent_chat_tasks import run_agent_chat_turn

    run_agent_chat_turn.delay(
        conversation_id=int(conversation.id),
        role_id=int(role.id),
        user_id=int(current_user.id),
        organization_id=int(org_id),
    )
    return response


def _draft_review_card(role: Role, summary: dict) -> dict:
    """A ``draft_task_review`` card focused on one (just-revised) draft."""
    return {
        "type": "draft_task_review",
        "role_id": int(role.id),
        "drafts": [summary],
        "reject_questions": REJECT_QUESTIONS,
    }


@router.post("/conversations/{role_id}/draft-tasks/{task_id}/approve")
def approve_draft_task(
    role_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve (activate) a generated draft from the chat. Narrates the outcome
    into the timeline so the recruiter sees the confirmation in-thread."""
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    result = approve_draft(db, role, task_id, user_id=int(current_user.id))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Approve failed")
    summary = result["summary"]
    post_agent_message(
        db,
        conversation=conversation,
        text=f"Approved **{summary['name']}** — it's live and assignable now.",
    )
    timeline = build_timeline(db, conversation=conversation, role=role)
    db.commit()
    return {"ok": True, "role_id": role.id, "summary": summary, "timeline": timeline}


@router.post("/conversations/{role_id}/draft-tasks/{task_id}/revise")
def revise_draft_task(
    role_id: int,
    task_id: int,
    body: ReviseDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Structured-reject → revise: re-author the draft from the recruiter's
    multiple-choice feedback (one metered call) instead of deleting it, then
    re-present the revised draft as a fresh review card in the timeline."""
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    api_key = str(getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    result = revise_draft(
        db, role, task_id, answers=body.answers or {}, note=body.note, api_key=api_key
    )
    if not result.get("ok"):
        post_agent_message(
            db,
            conversation=conversation,
            text=f"I couldn't revise that draft — {result.get('error')} The original is unchanged.",
        )
        timeline = build_timeline(db, conversation=conversation, role=role)
        db.commit()
        return {
            "ok": False,
            "role_id": role.id,
            "error": result.get("error"),
            "errors": result.get("errors"),
            "timeline": timeline,
        }
    summary = result["summary"]
    post_agent_message(
        db,
        conversation=conversation,
        text=f"Revised **{summary['name']}** from your feedback — take another look.",
        actions=[_draft_review_card(role, summary)],
    )
    timeline = build_timeline(db, conversation=conversation, role=role)
    db.commit()
    return {"ok": True, "role_id": role.id, "summary": summary, "timeline": timeline}


@router.post("/conversations/{role_id}/read")
def mark_conversation_read(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    mark_read(db, conversation=conversation, user=current_user)
    db.commit()
    return {"ok": True, "conversation_id": conversation.id}
