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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...agent_chat.engine import run_agent_turn
from ...agent_chat.service import (
    ensure_conversation,
    get_owned_role,
    list_agent_conversations,
    mark_read,
)
from ...agent_chat.timeline import build_timeline, serialize_message
from ...deps import get_current_user
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...platform.database import get_db

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


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


@router.get("/conversations/{role_id}/timeline")
def get_timeline(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _require_org(current_user)
    role = _require_role(db, role_id, org_id)
    conversation = ensure_conversation(db, organization_id=org_id, role=role)
    timeline = build_timeline(db, conversation=conversation, role=role)
    # Opening the thread marks it read (clears the unread badge).
    mark_read(db, conversation=conversation, user=current_user)
    db.commit()
    return {
        "conversation_id": conversation.id,
        "role_id": role.id,
        "role_name": role.name,
        "agent": _agent_meta(role),
        "timeline": timeline,
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

    try:
        new_messages = run_agent_turn(
            db=db,
            role=role,
            user=current_user,
            organization=organization,
            conversation=conversation,
            user_message=body.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Agent turn failed")

    # The sender has, by definition, read everything through their own send.
    mark_read(db, conversation=conversation, user=current_user)
    timeline = build_timeline(db, conversation=conversation, role=role)
    db.commit()
    return {
        "conversation_id": conversation.id,
        "role_id": role.id,
        "messages": [serialize_message(m) for m in new_messages],
        "timeline": timeline,
        "agent": _agent_meta(role),
    }


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
