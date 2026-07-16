"""Tool-registry and later-turn integration for new Agent Chat commands."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app.agent_chat.engine import persist_user_message, run_agent_response
from app.agent_chat.tools import AGENT_CHAT_TOOLS, dispatch_tool
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
    org = Organization(name="Command tools org", slug=f"command-tools-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"command-tools-{id(db)}@example.test",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=int(org.id),
        role="owner",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(
        organization_id=int(org.id),
        name="Backend",
        description="A complete backend engineering job specification.",
        source="manual",
    )
    db.add_all([user, role])
    db.flush()
    conversation = AgentConversation(
        organization_id=int(org.id), role_id=int(role.id)
    )
    db.add(conversation)
    db.flush()
    return user, role, conversation


def _persist_tool_result(db, *, conversation, body):
    row = AgentConversationMessage(
        conversation_id=int(conversation.id),
        organization_id=int(conversation.organization_id),
        role_id=int(conversation.role_id),
        author_role=AUTHOR_ROLE_USER,
        kind=MESSAGE_KIND_TOOL,
        content=[
            {
                "type": "tool_result",
                "tool_use_id": "tool-preview",
                "content": json.dumps(body),
                "is_error": False,
            }
        ],
    )
    db.add(row)
    db.flush()


def _persist_confirmation(db, *, conversation, user):
    row = AgentConversationMessage(
        conversation_id=int(conversation.id),
        organization_id=int(conversation.organization_id),
        role_id=int(conversation.role_id),
        author_role=AUTHOR_ROLE_USER,
        author_user_id=int(user.id),
        kind=MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": "Yes, proceed with that exact preview."}],
        text="Yes, proceed with that exact preview.",
    )
    db.add(row)
    db.flush()


def test_registry_exposes_every_new_command_once():
    names = [tool["name"] for tool in AGENT_CHAT_TOOLS]
    # The two related-role tools were added upstream while this command suite
    # was being built; keep the exact count aligned with the merged registry.
    assert len(names) == len(set(names)) == 36
    assert {
        "list_pending_decisions",
        "approve_decision",
        "override_decision",
        "snooze_decision",
        "re_evaluate_decision",
        "teach_decision",
        "get_helper_briefing",
        "list_recent_agent_runs",
        "list_open_recruiter_inputs",
        "answer_recruiter_input",
        "dismiss_recruiter_input",
        "create_application",
        "add_internal_note",
        "post_workable_note",
        "run_agent_now",
    }.issubset(names)


def test_approve_decision_previews_then_executes_after_later_confirmation(db):
    user, role, conversation = _world(db)
    snapshot = {
        "decision_id": 42,
        "application_id": 99,
        "candidate_name": "Ada Lovelace",
        "decision_type": "send_assessment",
        "recommendation": "send_assessment",
        "reasoning": "Strong match",
        "confidence": 0.91,
        "created_at": "2026-07-14T12:00:00+00:00",
        "snoozed_until": None,
        "can_approve": True,
        "approval_requires_workable_stage": False,
        "supported_alternatives": ["reject", "skip_assessment_advance"],
        "is_stale": False,
        "staleness_reasons": [],
        "staleness_summary": None,
    }
    with (
        patch(
            "app.agent_chat.tools._decision_commands.get_pending_decision",
            return_value=snapshot,
        ),
        patch(
            "app.agent_chat.tools._decision_commands.approve_decision",
            return_value={"status": "processing", "decision_id": 42},
        ) as execute,
    ):
        preview = dispatch_tool(
            "approve_decision",
            {"decision_id": 42, "note": "Strong evidence"},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "decision_action_preview"
        assert preview["needs_confirmation"] is True
        execute.assert_not_called()

        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)
        receipt = dispatch_tool(
            "approve_decision",
            {"decision_id": 42, "note": "Strong evidence"},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert receipt["type"] == "operation_receipt"
    assert receipt["status"] == "processing"
    assert receipt["_confirmation_consumed"]
    execute.assert_called_once_with(
        db,
        role,
        user,
        decision_id=42,
        note="Strong evidence",
        workable_target_stage=None,
    )


def test_teach_decision_previews_then_records_exact_confirmed_feedback(db):
    user, role, conversation = _world(db)
    snapshot = {
        "decision_id": 43,
        "application_id": 100,
        "candidate_name": "Grace Hopper",
        "decision_type": "reject",
        "recommendation": "reject",
        "status": "pending",
        "reasoning": "Missing evidence",
        "created_at": "2026-07-14T12:00:00+00:00",
    }
    arguments = {
        "decision_id": 43,
        "failure_mode": "missing_signal",
        "correction_text": "Use the verified portfolio evidence before rejecting.",
        "scope": "role",
        "attributed_to": "cv_scoring",
        "direction": "under",
    }
    with (
        patch(
            "app.agent_chat.tools._decision_teach.get_teachable_decision",
            return_value=snapshot,
        ),
        patch(
            "app.agent_chat.tools._decision_commands.teach_decision",
            return_value={
                "decision_status": "reverted_for_feedback",
                "feedback_id": 8,
                "cosign_required": False,
            },
        ) as execute,
    ):
        preview = dispatch_tool(
            "teach_decision",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "decision_action_preview"
        execute.assert_not_called()

        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)
        receipt = dispatch_tool(
            "teach_decision",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert receipt["type"] == "operation_receipt"
    assert receipt["result"]["feedback_id"] == 8
    execute.assert_called_once_with(db, role, user, **arguments)


def test_create_application_previews_then_uses_canonical_confirmed_arguments(db):
    user, role, conversation = _world(db)
    preview_data = {
        "type": "create_application_preview",
        "role_id": int(role.id),
        "candidate_email": "ada@example.com",
        "candidate_name": "Ada",
        "candidate_position": None,
        "candidate_exists": False,
        "candidate_id": None,
        "application_exists": False,
        "application_id": None,
        "would_update_candidate_profile": False,
        "can_create": True,
        "blocked_reason": None,
    }
    with (
        patch(
            "app.agent_chat.tools._application_commands.preview_create_application",
            return_value=preview_data,
        ),
        patch(
            "app.agent_chat.tools._application_commands.create_application",
            return_value={
                "status": "created",
                "application_id": 123,
                "candidate_id": 456,
                "candidate_email": "ada@example.com",
            },
        ) as execute,
    ):
        arguments = {"candidate_email": " ADA@example.com ", "candidate_name": "Ada"}
        preview = dispatch_tool(
            "create_application",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "operation_preview"
        execute.assert_not_called()

        _persist_tool_result(db, conversation=conversation, body=preview)
        _persist_confirmation(db, conversation=conversation, user=user)
        receipt = dispatch_tool(
            "create_application",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert receipt["type"] == "operation_receipt"
    assert receipt["result"]["application_id"] == 123
    execute.assert_called_once_with(
        db,
        role,
        user,
        candidate_email="ada@example.com",
        candidate_name="Ada",
        candidate_position=None,
        notes=None,
    )


def test_model_round_cannot_batch_two_state_changes(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Set the threshold and enable auto promote.",
    )

    def response(blocks, stop_reason):
        return SimpleNamespace(
            content=blocks,
            stop_reason=stop_reason,
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    tool_round = response(
        [
            SimpleNamespace(
                type="tool_use",
                id="threshold",
                name="set_threshold",
                input={"threshold": 65},
            ),
            SimpleNamespace(
                type="tool_use",
                id="settings",
                name="adjust_agent_settings",
                input={"auto_promote": True},
            ),
        ],
        "tool_use",
    )
    final_round = response(
        [SimpleNamespace(type="text", text="I need to run those one at a time.")],
        "end_turn",
    )

    with (
        patch("app.agent_chat.engine.get_client_for_org", return_value=object()),
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", side_effect=[tool_round, final_round]),
        patch("app.agent_chat.engine.dispatch_tool") as execute,
    ):
        assistant = run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    execute.assert_not_called()
    assert assistant.text == "I need to run those one at a time."
    tool_results = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_TOOL,
            AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
        )
        .order_by(AgentConversationMessage.id.desc())
        .first()
    )
    assert len(tool_results.content) == 2
    assert all(block["is_error"] is True for block in tool_results.content)
    assert all("one state-changing command" in block["content"] for block in tool_results.content)
