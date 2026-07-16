"""Related-role creation is available in both chats and requires a later yes."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

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
from app.models.chat_command_receipt import ChatCommandReceipt
from app.models.job_hiring_team import (
    TEAM_ROLE_INTERVIEWER,
    TEAM_ROLE_RECRUITER,
    JobHiringTeam,
)
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
        role="member",
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
    db.add(
        JobHiringTeam(
            organization_id=org.id,
            role_id=source.id,
            user_id=user.id,
            team_role=TEAM_ROLE_RECRUITER,
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


def _taali_tool_row(db, conversation, result):
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            role=ROLE_USER,
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "preview-taali",
                    "content": json.dumps(result),
                }
            ],
        )
    )
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
    copied_membership = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.role_id == created["role_id"],
            JobHiringTeam.user_id == user.id,
        )
        .one()
    )
    assert copied_membership.team_role == TEAM_ROLE_RECRUITER
    dispatch.assert_called_once()


def test_role_agent_crash_rolls_back_related_role_and_replay_creates_once(db):
    org, user, source = _seed(db)
    conversation = AgentConversation(
        organization_id=org.id,
        role_id=source.id,
        title="Related role crash",
    )
    db.add(conversation)
    db.commit()
    args = {"name": "AI Engineer · Recovery", "job_spec_text": SPEC}
    preview = dispatch_agent_tool(
        "preview_related_role",
        args,
        db=db,
        role=source,
        user=user,
        conversation=conversation,
    )
    _agent_tool_row(db, conversation, preview)
    db.add(
        AgentConversationMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role_id=source.id,
            author_role=AUTHOR_ROLE_USER,
            author_user_id=user.id,
            kind=MESSAGE_KIND_CHAT,
            content=[{"type": "text", "text": "Yes, create it."}],
            text="Yes, create it.",
        )
    )
    db.commit()

    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        with patch(
            "app.agent_chat.tools.complete_command",
            side_effect=RuntimeError("worker killed before receipt"),
        ):
            with pytest.raises(RuntimeError, match="worker killed"):
                dispatch_agent_tool(
                    "create_related_role",
                    args,
                    db=db,
                    role=source,
                    user=user,
                    conversation=conversation,
                )
        db.rollback()
        assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0

        created = dispatch_agent_tool(
            "create_related_role",
            args,
            db=db,
            role=source,
            user=user,
            conversation=conversation,
        )
        db.commit()

    assert created["type"] == "related_role_created"
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 1
    assert db.query(ChatCommandReceipt).count() == 1
    # The first delayed kick references rolled-back work and safely no-ops;
    # evaluation recovery remains the fallback if either publish is lost.
    assert dispatch.call_count == 2


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
    related_id = int(created["role_id"])
    assert (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.role_id == related_id,
            JobHiringTeam.user_id == user.id,
            JobHiringTeam.team_role == TEAM_ROLE_RECRUITER,
        )
        .count()
        == 1
    )


def test_agent_and_taali_confirmation_tables_are_isolated_even_when_ids_match(db):
    org, user, source = _seed(db)
    agent_conversation = AgentConversation(
        organization_id=org.id,
        role_id=source.id,
        title="Agent isolation",
    )
    taali_conversation = TaaliChatConversation(
        organization_id=org.id,
        user_id=user.id,
        role_id=source.id,
        title="Taali isolation",
    )
    db.add_all([agent_conversation, taali_conversation])
    db.commit()
    assert int(agent_conversation.id) == int(taali_conversation.id)
    args = {
        "role_id": source.id,
        "name": "AI Engineer · Isolation",
        "job_spec_text": SPEC,
    }

    taali_preview = dispatch_global_tool(
        "preview_related_role",
        args,
        db=db,
        user=user,
        conversation=taali_conversation,
    )
    _taali_tool_row(db, taali_conversation, taali_preview)
    db.add(
        AgentConversationMessage(
            conversation_id=agent_conversation.id,
            organization_id=org.id,
            role_id=source.id,
            author_role=AUTHOR_ROLE_USER,
            author_user_id=user.id,
            kind=MESSAGE_KIND_CHAT,
            content=[{"type": "text", "text": "Yes, create it."}],
            text="Yes, create it.",
        )
    )
    db.commit()
    blocked_agent = dispatch_agent_tool(
        "create_related_role",
        {"name": args["name"], "job_spec_text": SPEC},
        db=db,
        role=source,
        user=user,
        conversation=agent_conversation,
    )
    assert blocked_agent["type"] == "confirmation_required"


def test_agent_preview_cannot_be_confirmed_from_same_id_taali_conversation(db):
    org, user, source = _seed(db)
    agent_conversation = AgentConversation(
        organization_id=org.id,
        role_id=source.id,
        title="Agent isolation reverse",
    )
    taali_conversation = TaaliChatConversation(
        organization_id=org.id,
        user_id=user.id,
        role_id=source.id,
        title="Taali isolation reverse",
    )
    db.add_all([agent_conversation, taali_conversation])
    db.commit()
    assert int(agent_conversation.id) == int(taali_conversation.id)

    agent_args = {
        "name": "AI Engineer · Reverse isolation",
        "job_spec_text": SPEC,
    }
    agent_preview = dispatch_agent_tool(
        "preview_related_role",
        agent_args,
        db=db,
        role=source,
        user=user,
        conversation=agent_conversation,
    )
    _agent_tool_row(db, agent_conversation, agent_preview)
    db.add(
        TaaliChatMessage(
            conversation_id=taali_conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[{"type": "text", "text": "Yes, create it."}],
        )
    )
    db.commit()
    blocked_taali = dispatch_global_tool(
        "create_related_role",
        {"role_id": source.id, **agent_args},
        db=db,
        user=user,
        conversation=taali_conversation,
    )
    assert blocked_taali["type"] == "confirmation_required"
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0


def test_taali_confirmation_rejects_a_different_user(db):
    org, owner, source = _seed(db)
    other = User(
        email=f"other-related-{id(db)}@example.com",
        hashed_password="x",
        full_name="Other Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    conversation = TaaliChatConversation(
        organization_id=org.id,
        user_id=owner.id,
        role_id=source.id,
        title="Wrong user",
    )
    db.add_all([other, conversation])
    db.commit()
    args = {
        "role_id": source.id,
        "name": "AI Engineer · Owner only",
        "job_spec_text": SPEC,
    }
    preview = dispatch_global_tool(
        "preview_related_role",
        args,
        db=db,
        user=owner,
        conversation=conversation,
    )
    _taali_tool_row(db, conversation, preview)
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[{"type": "text", "text": "Yes, create it."}],
        )
    )
    db.commit()

    blocked = dispatch_global_tool(
        "create_related_role",
        args,
        db=db,
        user=other,
        conversation=conversation,
    )
    assert blocked["type"] == "confirmation_required"
    assert "different recruiter" in blocked["reason"]
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0


def test_taali_confirmation_requires_every_server_scope_binding(db):
    org, user, source = _seed(db)
    conversation = TaaliChatConversation(
        organization_id=org.id,
        user_id=user.id,
        role_id=source.id,
        title="Missing binding",
    )
    db.add(conversation)
    db.commit()
    args = {
        "role_id": source.id,
        "name": "AI Engineer · Bound",
        "job_spec_text": SPEC,
    }
    preview = dispatch_global_tool(
        "preview_related_role", args, db=db, user=user, conversation=conversation
    )
    preview["_confirmation"]["payload"].pop("requested_by_user_id")
    _taali_tool_row(db, conversation, preview)
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[{"type": "text", "text": "Yes, create it."}],
        )
    )
    db.commit()

    blocked = dispatch_global_tool(
        "create_related_role", args, db=db, user=user, conversation=conversation
    )
    assert blocked["type"] == "confirmation_required"
    assert "different recruiter" in blocked["reason"]


def test_taali_confirmation_cannot_switch_the_conversation_role(db):
    org, user, source = _seed(db)
    other_role = Role(
        organization_id=org.id,
        name="Other source role",
        source="manual",
        job_spec_text="A different complete source role specification.",
    )
    db.add(other_role)
    db.flush()
    conversation = TaaliChatConversation(
        organization_id=org.id,
        user_id=user.id,
        role_id=other_role.id,
        title="Pinned role",
    )
    db.add(conversation)
    db.commit()
    args = {
        "role_id": source.id,
        "name": "AI Engineer · Wrong thread",
        "job_spec_text": SPEC,
    }
    preview = dispatch_global_tool(
        "preview_related_role", args, db=db, user=user, conversation=conversation
    )
    _taali_tool_row(db, conversation, preview)
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[{"type": "text", "text": "Yes, create it."}],
        )
    )
    db.commit()

    blocked = dispatch_global_tool(
        "create_related_role", args, db=db, user=user, conversation=conversation
    )
    assert blocked["type"] == "confirmation_required"
    assert "different role conversation" in blocked["reason"]


def test_global_chat_related_role_preview_denies_unassigned_member(db):
    _org, user, source = _seed(db)
    db.query(JobHiringTeam).filter(
        JobHiringTeam.role_id == source.id,
        JobHiringTeam.user_id == user.id,
    ).delete(synchronize_session=False)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        dispatch_global_tool(
            "preview_related_role",
            {
                "role_id": source.id,
                "name": "AI Engineer · Search",
                "job_spec_text": SPEC,
            },
            db=db,
            user=user,
        )

    assert exc_info.value.status_code == 403
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0


@pytest.mark.parametrize("membership_state", ["unassigned", "interviewer"])
def test_global_chat_rechecks_related_role_permission_after_confirmation(
    db, membership_state
):
    org, user, source = _seed(db)
    conversation = TaaliChatConversation(
        organization_id=org.id,
        user_id=user.id,
        role_id=source.id,
        title="Revoked related role",
    )
    db.add(conversation)
    db.commit()
    args = {
        "role_id": source.id,
        "name": "AI Engineer · Retrieval",
        "job_spec_text": SPEC,
    }
    preview = dispatch_global_tool(
        "preview_related_role",
        args,
        db=db,
        user=user,
        conversation=conversation,
    )
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "revoked-preview",
                    "content": json.dumps(preview),
                }
            ],
        )
    )
    db.add(
        TaaliChatMessage(
            conversation_id=conversation.id,
            organization_id=org.id,
            role=ROLE_USER,
            content=[
                {"type": "text", "text": "Yes, create this related role."}
            ],
        )
    )
    db.commit()

    membership = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.role_id == source.id,
            JobHiringTeam.user_id == user.id,
        )
        .one()
    )
    if membership_state == "unassigned":
        db.delete(membership)
    else:
        membership.team_role = TEAM_ROLE_INTERVIEWER
    db.commit()

    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        with pytest.raises(HTTPException) as exc_info:
            dispatch_global_tool(
                "create_related_role",
                args,
                db=db,
                user=user,
                conversation=conversation,
            )

    assert exc_info.value.status_code == 403
    dispatch.assert_not_called()
    assert db.query(Role).filter(Role.role_kind == ROLE_KIND_SISTER).count() == 0
