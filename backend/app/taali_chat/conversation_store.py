"""Role-scoped Taali Chat conversation persistence.

Every chat turn uses these helpers to establish the organization, user, and
immutable role boundary before a candidate tool can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.taali_chat_message import TaaliChatMessage
from ..models.user import User


@dataclass
class ChatTurnInput:
    """Inputs for one user-to-assistant exchange."""

    user_message: str
    conversation_id: int | None = None
    # Used only when creating a conversation. Once stored, the conversation's
    # role is immutable for subsequent turns.
    role_id: int | None = None


def ensure_conversation(
    db: Session,
    *,
    user: User,
    conversation_id: int | None,
    first_message: str,
    role_id: int | None = None,
) -> TaaliChatConversation:
    """Return an owned conversation or create one with a validated role."""

    if conversation_id is not None:
        conversation = (
            db.query(TaaliChatConversation)
            .filter(
                TaaliChatConversation.id == conversation_id,
                TaaliChatConversation.organization_id == user.organization_id,
                TaaliChatConversation.user_id == user.id,
                TaaliChatConversation.archived_at.is_(None),
            )
            .first()
        )
        if conversation is None:
            raise ValueError(f"conversation {conversation_id} not found")
        return conversation

    safe_role_id: int | None = None
    if role_id is not None:
        from ..models.role import Role

        owned_role = (
            db.query(Role.id)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == user.organization_id,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if owned_role is None:
            # An explicitly role-bound request must never degrade into an
            # organization-wide conversation. Besides being surprising to the
            # caller, that fallback would re-expose model-supplied role ids and
            # let a failed role lookup escape the server-owned scope boundary.
            raise ValueError(f"role {int(role_id)} not found")
        safe_role_id = int(role_id)

    title = first_message.strip().split("\n", 1)[0][:80] or "New conversation"
    conversation = TaaliChatConversation(
        organization_id=user.organization_id,
        user_id=user.id,
        role_id=safe_role_id,
        title=title,
    )
    db.add(conversation)
    db.flush()
    return conversation


def load_history(
    db: Session, *, conversation: TaaliChatConversation
) -> list[dict[str, Any]]:
    """Load a transcript after removing orphaned provider tool-use pairs."""

    from ..llm.tool_pairs import sanitize_tool_pairs

    rows = (
        db.query(TaaliChatMessage)
        .filter(TaaliChatMessage.conversation_id == conversation.id)
        .order_by(TaaliChatMessage.created_at.asc(), TaaliChatMessage.id.asc())
        .all()
    )
    return sanitize_tool_pairs(
        [{"role": row.role, "content": row.content} for row in rows]
    )


def persist_message(
    db: Session,
    *,
    conversation: TaaliChatConversation,
    role: str,
    content: list[dict[str, Any]],
    model: str | None = None,
    stop_reason: str | None = None,
    token_usage: dict[str, int] | None = None,
) -> TaaliChatMessage:
    """Append and flush one transcript message without committing the turn."""

    message = TaaliChatMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role=role,
        content=content,
        model=model,
        stop_reason=stop_reason,
        token_usage=token_usage,
    )
    db.add(message)
    db.flush()
    return message


__all__ = ["ChatTurnInput", "ensure_conversation", "load_history", "persist_message"]
