"""Related-role creation is available in both chats and requires a later yes."""

from __future__ import annotations

import json
from unittest.mock import patch

from app.agent_chat.tools import dispatch_tool as dispatch_agent_tool
from app.models.agent_conversation import (
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.taali_chat_message import ROLE_USER, TaaliChatMessage
from app.models.user import User
from app.taali_chat.tool_registry import dispatch_tool as dispatch_global_tool


SPEC = (
    "Senior AI engineer owning production RAG systems, evaluation design, "
    "Python services, distributed inference, observability, and model reliability."
)


def _seed(db):
    org = Organization(name="Related Chat Org", slug=f"related-chat-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"related-chat-{id(db)}@example.com",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    source = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="workable",
        workable_job_id="AI-ENG",
        job_spec_text="Original AI engineer job specification.",
    )
    db.add_all([user, source])
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"candidate-{id(db)}@example.com",
        full_name="Candidate One",
        cv_text="Python engineer who shipped and evaluated production RAG systems.",
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=org.id,
            candidate_id=candidate.id,
            role_id=source.id,
            source="workable",
            workable_candidate_id="candidate-one",
            application_outcome="open",
            cv_text=candidate.cv_text,
        )
    )
    db.commit()
    return org, user, source


def _agent_tool_row(db, conversation, result):
    row = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role_id=conversation.role_id,
        author_role=AUTHOR_ROLE_USER,
        kind=MESSAGE_KIND_TOOL,
        content=[
            {
                "type": "tool_result",
                "tool_use_id": "preview-1",
                "content": json.dumps(result),
            }
        ],
    )
    db.add(row)
    db.commit()


def test_role_agent_previews_then_creates_only_after_later_confirmation(db):
    org, user, source = _seed(db)
    conversation = AgentConversation(
        organization_id=org.id, role_id=source.id, title="Related role"
    )
    db.add(conversation)
    db.commit()

    args = {"name": "AI Engineer · RAG", "job_spec_text": SPEC}
    preview = dispatch_agent_tool(
        "preview_related_role",
        args,
        db=db,
        role=source,
        user=user,
        conversation=conversation,
    )
    assert preview["type"] == "related_role_preview"
    assert preview["candidates_total"] == 1
    assert preview["needs_confirmation"] is True
    _agent_tool_row(db, conversation, preview)

    blocked = dispatch_agent_tool(
        "create_related_role",
        args,
        db=db,
        role=source,
        user=user,
        conversation=conversation,
    )
    assert blocked["type"] == "confirmation_required"
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0

    db.add(
        AgentConversationMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role_id=source.id,
            author_role=AUTHOR_ROLE_USER,
            author_user_id=user.id,
            kind=MESSAGE_KIND_CHAT,
            content=[{"type": "text", "text": "Yes, go ahead."}],
            text="Yes, go ahead.",
        )
    )
    db.commit()
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        created = dispatch_agent_tool(
            "create_related_role",
            args,
            db=db,
            role=source,
            user=user,
            conversation=conversation,
        )

    assert created["type"] == "related_role_created"
    assert created["source_role_id"] == source.id
    assert created["evaluation_counts"] == {"total": 1, "pending": 1, "unscorable": 0}
    dispatch.assert_called_once()


def test_global_chat_uses_the_same_preview_and_later_confirmation_guard(db):
    org, user, source = _seed(db)
    conversation = TaaliChatConversation(
        organization_id=org.id, user_id=user.id, role_id=source.id, title="New role"
    )
    db.add(conversation)
    db.commit()
    args = {"role_id": source.id, "name": "AI Engineer · Platform", "job_spec_text": SPEC}

    preview = dispatch_global_tool(
        "preview_related_role", args, db=db, user=user, conversation=conversation
    )
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "preview-global",
                    "content": json.dumps(preview),
                }
            ],
        )
    )
    db.commit()
    blocked = dispatch_global_tool(
        "create_related_role", args, db=db, user=user, conversation=conversation
    )
    assert blocked["type"] == "confirmation_required"

    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[{"type": "text", "text": "Confirm, create it."}],
        )
    )
    db.commit()
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ):
        created = dispatch_global_tool(
            "create_related_role", args, db=db, user=user, conversation=conversation
        )
    assert created["type"] == "related_role_created"
    assert created["frontend_url"].startswith("/jobs/")
