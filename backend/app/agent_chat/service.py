"""Conversation management for the role-agent chat.

Owns the lifecycle of one ``AgentConversation`` per (org, role) and the
sidebar query that lists every *active agent* with its attention counts —
unread agent messages, open questions, pending decisions — which drives the
left rail and the notification badge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AgentConversation,
    AgentConversationMessage,
    AgentConversationRead,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
)
from ..models.agent_decision import AgentDecision
from ..models.agent_needs_input import AgentNeedsInput
from ..models.role import Role
from ..models.user import User

_VISIBLE_MESSAGE_KINDS = (MESSAGE_KIND_CHAT, MESSAGE_KIND_ACTION)


def post_agent_message(
    db: Session,
    *,
    conversation: AgentConversation,
    text: str,
    actions: list[dict[str, Any]] | None = None,
) -> AgentConversationMessage:
    """Append a plain agent (assistant) message to the conversation.

    For non-LLM actions (draft-task approve/revise) that need to narrate an
    outcome and optionally attach a card into the timeline without running a
    full agent turn. Flushes so the id populates; the caller commits.
    """
    msg = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role_id=conversation.role_id,
        author_role=AUTHOR_ROLE_ASSISTANT,
        kind=MESSAGE_KIND_ACTION if actions else MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": text}],
        text=text,
        actions=actions or None,
    )
    db.add(msg)
    db.flush()
    return msg


def get_owned_role(db: Session, *, role_id: int, organization_id: int) -> Role | None:
    return (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .first()
    )


def ensure_conversation(
    db: Session, *, organization_id: int, role: Role
) -> AgentConversation:
    """Get (or lazily create) the role's conversation."""
    convo = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.organization_id == int(organization_id),
            AgentConversation.role_id == int(role.id),
        )
        .first()
    )
    if convo is not None:
        return convo
    convo = AgentConversation(
        organization_id=int(organization_id),
        role_id=int(role.id),
        title=role.name,
    )
    db.add(convo)
    db.flush()
    return convo


def mark_read(db: Session, *, conversation: AgentConversation, user: User) -> None:
    now = datetime.now(timezone.utc)
    row = (
        db.query(AgentConversationRead)
        .filter(
            AgentConversationRead.conversation_id == conversation.id,
            AgentConversationRead.user_id == int(user.id),
        )
        .first()
    )
    if row is None:
        db.add(
            AgentConversationRead(
                conversation_id=conversation.id,
                user_id=int(user.id),
                last_read_at=now,
            )
        )
    else:
        row.last_read_at = now
    db.flush()


def _counts_by_role(db: Session, role_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    """(pending_decisions_by_role, open_questions_by_role) for the given roles."""
    if not role_ids:
        return {}, {}
    pending = {
        int(rid): int(n)
        for rid, n in (
            db.query(AgentDecision.role_id, func.count(AgentDecision.id))
            .filter(
                AgentDecision.role_id.in_(role_ids),
                AgentDecision.status == "pending",
            )
            .group_by(AgentDecision.role_id)
            .all()
        )
    }
    questions = {
        int(rid): int(n)
        for rid, n in (
            db.query(AgentNeedsInput.role_id, func.count(AgentNeedsInput.id))
            .filter(
                AgentNeedsInput.role_id.in_(role_ids),
                AgentNeedsInput.resolved_at.is_(None),
                AgentNeedsInput.dismissed_at.is_(None),
            )
            .group_by(AgentNeedsInput.role_id)
            .all()
        )
    }
    return pending, questions


def _is_live_role(role: Role) -> bool:
    """A role is 'live' when its Workable job is published (actively recruiting)
    — mirrors the Jobs page's Live filter. `workable_job_state` isn't a column;
    it's derived from the cached `workable_job_data` blob, so we read it here in
    Python (the same JSON test the serializer uses)."""
    data = getattr(role, "workable_job_data", None)
    state = (data.get("state") if isinstance(data, dict) else None) or ""
    return str(state).strip().lower() == "published"


def list_agent_conversations(
    db: Session, *, organization_id: int, user: User
) -> list[dict[str, Any]]:
    """Sidebar list: every LIVE role (so the recruiter can activate an agent on
    any of them straight from Home), plus any agent-on role and any role with a
    started thread — with attention counts + last-message preview, most-active
    first."""
    convo_role_ids = {
        int(r[0])
        for r in db.query(AgentConversation.role_id)
        .filter(AgentConversation.organization_id == int(organization_id))
        .all()
    }
    # Liveness is a JSON-derived predicate (can't filter in SQL portably), so
    # load the org's non-deleted roles and keep live / agent-on / has-thread.
    roles = [
        r
        for r in (
            db.query(Role)
            .filter(
                Role.organization_id == int(organization_id),
                Role.deleted_at.is_(None),
            )
            .all()
        )
        if _is_live_role(r) or bool(r.agentic_mode_enabled) or int(r.id) in convo_role_ids
    ]
    if not roles:
        return []
    role_by_id = {int(r.id): r for r in roles}
    role_ids = list(role_by_id.keys())

    convos = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.organization_id == int(organization_id),
            AgentConversation.role_id.in_(role_ids),
        )
        .all()
    )
    convo_by_role = {int(c.role_id): c for c in convos}
    convo_ids = [int(c.id) for c in convos]

    reads = {
        int(r.conversation_id): r.last_read_at
        for r in (
            db.query(AgentConversationRead)
            .filter(
                AgentConversationRead.conversation_id.in_(convo_ids or [0]),
                AgentConversationRead.user_id == int(user.id),
            )
            .all()
        )
    }

    # All visible messages for these conversations, to compute the preview +
    # unread count in one pass.
    msg_rows = (
        db.query(
            AgentConversationMessage.conversation_id,
            AgentConversationMessage.author_role,
            AgentConversationMessage.text,
            AgentConversationMessage.created_at,
        )
        .filter(
            AgentConversationMessage.conversation_id.in_(convo_ids or [0]),
            AgentConversationMessage.kind.in_(_VISIBLE_MESSAGE_KINDS),
        )
        .order_by(AgentConversationMessage.created_at.asc(), AgentConversationMessage.id.asc())
        .all()
    )
    last_preview: dict[int, tuple[str, Any]] = {}
    unread: dict[int, int] = {}
    for cid, author_role, text, created_at in msg_rows:
        cid = int(cid)
        last_preview[cid] = ((text or "").strip(), created_at)
        if author_role == "assistant":
            last_read = reads.get(cid)
            if last_read is None or (created_at and created_at > last_read):
                unread[cid] = unread.get(cid, 0) + 1

    pending_by_role, questions_by_role = _counts_by_role(db, role_ids)

    items: list[dict[str, Any]] = []
    for rid, role in role_by_id.items():
        convo = convo_by_role.get(rid)
        cid = int(convo.id) if convo else None
        preview, preview_at = last_preview.get(cid, ("", None)) if cid else ("", None)
        unread_n = unread.get(cid, 0) if cid else 0
        open_q = questions_by_role.get(rid, 0)
        pending_d = pending_by_role.get(rid, 0)
        last_at = (convo.last_message_at if convo else None) or preview_at
        items.append(
            {
                "role_id": rid,
                "role_name": role.name,
                "conversation_id": cid,
                "agent_enabled": bool(role.agentic_mode_enabled),
                "agent_paused": role.agent_paused_at is not None,
                "agent_paused_reason": role.agent_paused_reason,
                "unread_messages": unread_n,
                "open_questions": open_q,
                "pending_decisions": pending_d,
                # Notification badge: things the agent is waiting on you for.
                "attention": unread_n + open_q + pending_d,
                "last_message_preview": preview[:140],
                "last_message_at": last_at.isoformat() if last_at else None,
            }
        )

    # Most-active first: recent activity, then attention count.
    items.sort(
        key=lambda it: (it["last_message_at"] or "", it["attention"]),
        reverse=True,
    )
    return items


__all__ = [
    "ensure_conversation",
    "get_owned_role",
    "list_agent_conversations",
    "mark_read",
    "post_agent_message",
]
