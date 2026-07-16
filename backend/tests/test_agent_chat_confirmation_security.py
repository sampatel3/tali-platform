"""Security properties of Agent Chat's persisted confirmation receipts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.agent_chat.confirmations import (
    attach_confirmation,
    require_later_turn_confirmation,
)
from app.models.agent_conversation import (
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


def _world(db):
    org = Organization(name="Confirmation org", slug=f"confirmation-{id(db)}")
    db.add(org)
    db.flush()
    first = User(
        email=f"confirmation-first-{id(db)}@example.test",
        hashed_password="x",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    second = User(
        email=f"confirmation-second-{id(db)}@example.test",
        hashed_password="x",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(organization_id=int(org.id), name="Backend", source="manual")
    db.add_all([first, second, role])
    db.flush()
    conversation = AgentConversation(
        organization_id=int(org.id),
        role_id=int(role.id),
    )
    db.add(conversation)
    db.flush()
    return org, first, second, role, conversation


def _persist_preview(db, *, conversation, receipt):
    row = AgentConversationMessage(
        conversation_id=int(conversation.id),
        organization_id=int(conversation.organization_id),
        role_id=int(conversation.role_id),
        author_role=AUTHOR_ROLE_USER,
        kind=MESSAGE_KIND_TOOL,
        content=[
            {
                "type": "tool_result",
                "tool_use_id": "preview",
                "content": json.dumps(receipt),
                "is_error": False,
            }
        ],
    )
    db.add(row)
    db.flush()
    return row


def _persist_user(db, *, conversation, user, text="yes, proceed"):
    row = AgentConversationMessage(
        conversation_id=int(conversation.id),
        organization_id=int(conversation.organization_id),
        role_id=int(conversation.role_id),
        author_role=AUTHOR_ROLE_USER,
        author_user_id=int(user.id),
        kind=MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": text}],
        text=text,
    )
    db.add(row)
    db.flush()
    return row


def test_only_the_recruiter_bound_to_the_preview_can_confirm(db):
    org, first, second, role, conversation = _world(db)
    receipt = attach_confirmation(
        {"type": "preview"},
        operation="approve_decision:42",
        payload={
            "organization_id": int(org.id),
            "conversation_id": int(conversation.id),
            "requested_by_user_id": int(first.id),
            "role_id": int(role.id),
        },
    )
    _persist_preview(db, conversation=conversation, receipt=receipt)
    _persist_user(db, conversation=conversation, user=second)

    other_check = require_later_turn_confirmation(
        db,
        conversation=conversation,
        operation="approve_decision:42",
        user=first,
    )
    assert other_check.ok is False
    assert "later message" in other_check.reason

    _persist_user(db, conversation=conversation, user=first)
    owner_check = require_later_turn_confirmation(
        db,
        conversation=conversation,
        operation="approve_decision:42",
        user=first,
    )
    assert owner_check.ok is True


def test_expired_preview_cannot_authorize_an_operation(db):
    org, first, _second, role, conversation = _world(db)
    receipt = attach_confirmation(
        {"type": "preview"},
        operation="rescreen_role",
        payload={
            "organization_id": int(org.id),
            "conversation_id": int(conversation.id),
            "requested_by_user_id": int(first.id),
            "role_id": int(role.id),
        },
    )
    receipt["_confirmation"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    _persist_preview(db, conversation=conversation, receipt=receipt)
    _persist_user(db, conversation=conversation, user=first)

    check = require_later_turn_confirmation(
        db,
        conversation=conversation,
        operation="rescreen_role",
        user=first,
    )
    assert check.ok is False
    assert "expired" in check.reason
