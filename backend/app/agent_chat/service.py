"""Conversation management for the role-agent chat.

Owns the lifecycle of one ``AgentConversation`` per (org, role) and the
sidebar query that lists every *active agent* with its attention counts —
unread agent messages, open questions, pending decisions — which drives the
left rail and the notification badge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AUTHOR_ROLE_USER,
    AgentConversation,
    AgentConversationMessage,
    AgentConversationRead,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_EVENT,
    MESSAGE_KIND_PROACTIVE,
)
from ..models.agent_decision import AgentDecision
from ..models.agent_needs_input import AgentNeedsInput
from ..models.role import Role
from ..models.user import User
from ..services.workspace_agent_control import workspace_agent_pause_state

_VISIBLE_MESSAGE_KINDS = (
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_PROACTIVE,
    MESSAGE_KIND_EVENT,
)
_INTERACTIVE_MESSAGE_KINDS = (MESSAGE_KIND_CHAT, MESSAGE_KIND_ACTION)


def post_agent_message(
    db: Session,
    *,
    conversation: AgentConversation,
    text: str,
    actions: list[dict[str, Any]] | None = None,
    kind: str | None = None,
    stop_reason: str | None = None,
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
        kind=kind or (MESSAGE_KIND_ACTION if actions else MESSAGE_KIND_CHAT),
        content=[{"type": "text", "text": text}],
        text=text,
        actions=actions or None,
        stop_reason=stop_reason,
    )
    db.add(msg)
    now = datetime.now(timezone.utc)
    conversation.last_message_at = now
    conversation.updated_at = now
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
    try:
        # Concurrent background events can be the first thing to create a
        # role thread. Contain a losing unique-key insert in a savepoint, then
        # load the winner instead of poisoning the caller's domain transaction.
        with db.begin_nested():
            db.add(convo)
            db.flush()
        return convo
    except IntegrityError:
        return (
            db.query(AgentConversation)
            .filter(
                AgentConversation.organization_id == int(organization_id),
                AgentConversation.role_id == int(role.id),
            )
            .one()
        )


# A turn is "running" while the last visible message is the recruiter's and the
# agent hasn't replied yet. Self-clearing: the async worker always posts a reply
# (an answer or an error) to close a turn, which flips this off — no extra
# column. If a worker dies mid-turn it ages out after this window so the
# composer never locks forever.
AGENT_TURN_TIMEOUT = timedelta(minutes=5)


def conversation_agent_working(db: Session, conversation: AgentConversation) -> bool:
    """True when a turn is in flight for this conversation — i.e. the last
    visible message is a recent recruiter message the agent hasn't answered yet.

    Drives the durable "agent is working…" indicator: it's recomputed from
    persisted state on every timeline read, so it survives navigation and an
    agent switch (unlike a request-scoped spinner).
    """
    # The durable receipt is authoritative for newly-created turns. It remains
    # true across broker loss/navigation and is cleared only when a worker posts
    # a reply. Legacy conversations fall through to transcript inference.
    if getattr(conversation, "turn_status", None) in ("pending", "running"):
        return True

    last = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == conversation.id,
            # Ignore agent-initiated helper prompts here. Only an interactive
            # assistant reply can close the latest recruiter turn.
            AgentConversationMessage.kind.in_(_INTERACTIVE_MESSAGE_KINDS),
        )
        .order_by(
            AgentConversationMessage.created_at.desc(),
            AgentConversationMessage.id.desc(),
        )
        .first()
    )
    if last is None or last.author_role != AUTHOR_ROLE_USER:
        return False
    created = last.created_at
    if created is None:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created) < AGENT_TURN_TIMEOUT


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


# Agent-first grouping of the sidebar list — each role lands in the FIRST section
# it matches, so the agents you're actively running sit at the top.
_GROUP_ORDER = {"on_paused": 0, "previously_on": 1, "starred": 2, "active": 3}


def _agent_group(role: Role) -> str:
    """Which section this role belongs in (first match wins):
      on_paused     — agent on or paused (you're actively running it)
      previously_on — the agent RAN before but is off now (agent_last_run_at set)
      starred       — a starred role with no current/past agent
      active        — any other (live) role in the list
    """
    if bool(role.agentic_mode_enabled) or role.agent_paused_at is not None:
        return "on_paused"
    if role.agent_last_run_at is not None:
        return "previously_on"
    if bool(getattr(role, "starred_for_auto_sync", False)):
        return "starred"
    return "active"


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
        if (
            _is_live_role(r)
            or bool(r.agentic_mode_enabled)
            or r.agent_paused_at is not None
            or r.agent_last_run_at is not None
            or bool(getattr(r, "starred_for_auto_sync", False))
            or int(r.id) in convo_role_ids
        )
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
    # The workspace switch is an execution overlay, not a bulk edit of every
    # role. Resolve it once for the whole sidebar response, then expose both
    # the effective state and the untouched role-local desired state on each
    # row. This prevents chat surfaces from animating an agent as running while
    # the workspace has denied autonomous work.
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(organization_id),
        current_user_id=int(user.id),
    )
    workspace_paused = bool(workspace_pause["paused"])

    items: list[dict[str, Any]] = []
    for rid, role in role_by_id.items():
        convo = convo_by_role.get(rid)
        cid = int(convo.id) if convo else None
        preview, preview_at = last_preview.get(cid, ("", None)) if cid else ("", None)
        unread_n = unread.get(cid, 0) if cid else 0
        open_q = questions_by_role.get(rid, 0)
        pending_d = pending_by_role.get(rid, 0)
        last_at = (convo.last_message_at if convo else None) or preview_at
        agent_enabled = bool(role.agentic_mode_enabled)
        role_paused = role.agent_paused_at is not None
        effective_paused = agent_enabled and (workspace_paused or role_paused)
        pause_scope = (
            "workspace"
            if effective_paused and workspace_paused
            else ("role" if effective_paused and role_paused else None)
        )
        effective_paused_at = (
            workspace_pause["paused_at"]
            if pause_scope == "workspace"
            else (role.agent_paused_at if pause_scope == "role" else None)
        )
        effective_paused_reason = (
            workspace_pause["reason"]
            if pause_scope == "workspace"
            else (role.agent_paused_reason if pause_scope == "role" else None)
        )
        items.append(
            {
                "role_id": rid,
                "role_name": role.name,
                "conversation_id": cid,
                # ``agent_enabled`` remains the role's desired state. Legacy
                # ``agent_paused*`` fields are effective (workspace > role),
                # matching the role status API, while explicit local fields
                # preserve what will happen after the workspace resumes.
                "agent_enabled": agent_enabled,
                "agent_running": agent_enabled and not effective_paused,
                "agent_paused": effective_paused,
                "agent_effective_paused": effective_paused,
                "agent_pause_scope": pause_scope,
                "agent_paused_at": effective_paused_at,
                "agent_paused_reason": effective_paused_reason,
                "role_paused": role_paused,
                "role_paused_at": role.agent_paused_at,
                "role_paused_reason": role.agent_paused_reason,
                "workspace_paused": workspace_paused,
                "workspace_paused_at": workspace_pause["paused_at"],
                "workspace_paused_reason": workspace_pause["reason"],
                "workspace_paused_by": workspace_pause["paused_by"],
                "workspace_control_version": int(workspace_pause["version"]),
                # Grouping signals (agent-first sections, computed once here).
                "group": _agent_group(role),
                "starred": bool(getattr(role, "starred_for_auto_sync", False)),
                "is_live": _is_live_role(role),
                "ever_ran": role.agent_last_run_at is not None,
                "unread_messages": unread_n,
                "open_questions": open_q,
                "pending_decisions": pending_d,
                # Notification badge: things the agent is waiting on you for.
                "attention": unread_n + open_q + pending_d,
                "last_message_preview": preview[:140],
                "last_message_at": last_at.isoformat() if last_at else None,
            }
        )

    # Within a group: most-active first (recent activity, then attention). Then a
    # stable sort by group order puts the agents you're running on top while
    # preserving that within-group recency.
    items.sort(key=lambda it: (it["last_message_at"] or "", it["attention"]), reverse=True)
    items.sort(key=lambda it: _GROUP_ORDER.get(it["group"], 9))
    return items


__all__ = [
    "conversation_agent_working",
    "ensure_conversation",
    "get_owned_role",
    "list_agent_conversations",
    "mark_read",
    "post_agent_message",
]
