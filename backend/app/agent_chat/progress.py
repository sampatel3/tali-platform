"""Persisted, user-facing progress labels for role-agent turns."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)

_INTERACTIVE_MESSAGE_KINDS = (MESSAGE_KIND_CHAT, MESSAGE_KIND_ACTION)


def conversation_agent_progress(
    db: Session,
    conversation: AgentConversation,
    *,
    working: bool,
) -> str | None:
    """Return a truthful stage label for an in-flight turn.

    Hidden tool rows are committed at paid-call boundaries by the engine, so
    timeline polling can describe observable work without exposing model
    reasoning or maintaining a second task-state table.
    """
    if not working:
        return None

    last_user = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == conversation.id,
            AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
            AgentConversationMessage.kind.in_(_INTERACTIVE_MESSAGE_KINDS),
        )
        .order_by(
            AgentConversationMessage.created_at.desc(),
            AgentConversationMessage.id.desc(),
        )
        .first()
    )
    if last_user is None:
        return "Understanding your request…"

    latest_tool = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == conversation.id,
            AgentConversationMessage.kind == MESSAGE_KIND_TOOL,
            AgentConversationMessage.id > int(last_user.id),
        )
        .order_by(AgentConversationMessage.id.desc())
        .first()
    )
    if latest_tool is None:
        return "Understanding your request…"
    if latest_tool.author_role != AUTHOR_ROLE_ASSISTANT:
        return "Preparing your answer…"

    tool_names = {
        str(block.get("name") or "").lower()
        for block in (latest_tool.content or [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    if any("candidate" in name or "application" in name for name in tool_names):
        return "Searching and ranking candidates…"
    if any(
        token in name
        for name in tool_names
        for token in ("role", "constraint", "threshold", "requirement")
    ):
        return "Reviewing the role and its requirements…"
    if any("decision" in name for name in tool_names):
        return "Checking pending decisions…"
    return "Checking the latest role data…"


__all__ = ["conversation_agent_progress"]
