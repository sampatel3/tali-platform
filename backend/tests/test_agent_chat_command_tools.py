"""Tool-registry and later-turn integration for new Agent Chat commands."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent_chat.engine import persist_user_message, run_agent_response
from app.agent_chat.tools import AGENT_CHAT_TOOLS, MUTATING_TOOL_NAMES, dispatch_tool
from app.models.agent_conversation import (
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.background_job_run import BackgroundJobRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.chat_command_receipt import ChatCommandReceipt
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
    # Keep the exact count aligned with the merged registry and prove both
    # explicit-report publication and related-draft handoff remain available.
    assert len(names) == len(set(names)) == 38
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
        "create_top_candidates_report",
        "start_related_role_draft",
    }.issubset(names)
    assert "create_related_role" in MUTATING_TOOL_NAMES


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


def test_decision_domain_commit_recovers_pending_receipt_without_replaying(db):
    user, role, conversation = _world(db)
    candidate = Candidate(
        organization_id=role.organization_id,
        email=f"receipt-decision-{id(db)}@example.com",
        full_name="Receipt Candidate",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=role.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="manual",
        application_outcome="open",
    )
    db.add(application)
    db.flush()
    from app.models.agent_decision import AgentDecision

    decision = AgentDecision(
        organization_id=role.organization_id,
        role_id=role.id,
        application_id=application.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="Strong evidence",
        model_version="test",
        prompt_version="test",
        idempotency_key=f"receipt-decision:{application.id}",
    )
    db.add(decision)
    db.commit()
    snapshot = {
        "decision_id": int(decision.id),
        "application_id": int(application.id),
        "candidate_name": "Receipt Candidate",
        "decision_type": "send_assessment",
        "recommendation": "send_assessment",
        "reasoning": "Strong evidence",
        "confidence": 0.9,
        "created_at": "2026-07-14T12:00:00+00:00",
        "snoozed_until": None,
        "can_approve": True,
        "approval_requires_workable_stage": False,
        "supported_alternatives": ["reject"],
        "is_stale": False,
        "staleness_reasons": [],
        "staleness_summary": None,
    }
    arguments = {"decision_id": int(decision.id), "note": "Proceed"}
    with patch(
        "app.agent_chat.tools._decision_commands.get_pending_decision",
        return_value=snapshot,
    ):
        preview = dispatch_tool(
            "approve_decision",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
    _persist_tool_result(db, conversation=conversation, body=preview)
    _persist_confirmation(db, conversation=conversation, user=user)
    db.commit()

    def _commit_domain(db, *_args, **_kwargs):
        row = db.get(AgentDecision, int(decision.id))
        row.status = "processing"
        db.commit()
        return {"status": "processing", "decision_id": int(row.id)}

    with patch(
        "app.agent_chat.tools._decision_commands.get_pending_decision",
        return_value=snapshot,
    ), patch(
        "app.agent_chat.tools._decision_commands.approve_decision",
        side_effect=_commit_domain,
    ) as execute, patch(
        "app.agent_chat.tools.complete_command",
        side_effect=RuntimeError("worker died after domain commit"),
    ):
        with pytest.raises(RuntimeError, match="worker died"):
            dispatch_tool(
                "approve_decision",
                arguments,
                db=db,
                role=role,
                user=user,
                conversation=conversation,
            )
    db.rollback()
    assert db.query(ChatCommandReceipt).one().status == "pending"

    with patch(
        "app.agent_chat.tools._decision_commands.approve_decision"
    ) as replay_execute:
        recovered = dispatch_tool(
            "approve_decision",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()
    assert recovered["status"] == "processing"
    assert recovered["result"]["recovered"] is True
    replay_execute.assert_not_called()
    assert execute.call_count == 1
    assert db.query(ChatCommandReceipt).one().status == "completed"


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


def test_caught_local_mutation_error_does_not_commit_pending_command_receipt(db):
    user, role, conversation = _world(db)
    preview_data = {
        "type": "create_application_preview",
        "role_id": int(role.id),
        "candidate_email": "error@example.com",
        "candidate_name": "Error Candidate",
        "candidate_position": None,
        "candidate_exists": False,
        "candidate_id": None,
        "application_exists": False,
        "application_id": None,
        "would_update_candidate_profile": False,
        "can_create": True,
        "blocked_reason": None,
    }
    arguments = {
        "candidate_email": "error@example.com",
        "candidate_name": "Error Candidate",
    }
    with patch(
        "app.agent_chat.tools._application_commands.preview_create_application",
        return_value=preview_data,
    ):
        preview = dispatch_tool(
            "create_application",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
    _persist_tool_result(db, conversation=conversation, body=preview)
    _persist_confirmation(db, conversation=conversation, user=user)
    db.commit()

    with patch(
        "app.agent_chat.tools._application_commands.preview_create_application",
        return_value=preview_data,
    ), patch(
        "app.agent_chat.tools._application_commands.create_application",
        side_effect=RuntimeError("local mutation failed"),
    ):
        with pytest.raises(RuntimeError, match="local mutation failed"):
            dispatch_tool(
                "create_application",
                arguments,
                db=db,
                role=role,
                user=user,
                conversation=conversation,
            )
    # Mirrors the engine catching the exception and committing a safe error
    # tool-result: the new pending receipt must not hitchhike on that commit.
    db.commit()
    assert db.query(ChatCommandReceipt).count() == 0


def test_workable_note_crash_replay_reuses_one_durable_dispatch(db):
    user, role, conversation = _world(db)
    candidate = Candidate(
        organization_id=int(role.organization_id),
        email="note-crash@example.test",
        full_name="Note Crash",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(role.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        source="workable",
        workable_candidate_id="workable-note-crash",
        application_outcome="open",
    )
    db.add(application)
    db.commit()
    arguments = {
        "application_id": int(application.id),
        "body": "Private salary context that must be posted once.",
    }
    preview = dispatch_tool(
        "post_workable_note",
        arguments,
        db=db,
        role=role,
        user=user,
        conversation=conversation,
    )
    _persist_tool_result(db, conversation=conversation, body=preview)
    _persist_confirmation(db, conversation=conversation, user=user)
    db.commit()

    with patch("app.tasks.workable_tasks.run_workable_op_task.apply_async") as publish:
        with patch(
            "app.agent_chat.tools.complete_command",
            side_effect=RuntimeError("worker killed before tool result"),
        ):
            with pytest.raises(RuntimeError, match="worker killed"):
                dispatch_tool(
                    "post_workable_note",
                    arguments,
                    db=db,
                    role=role,
                    user=user,
                    conversation=conversation,
                )
        db.rollback()

        receipt = dispatch_tool(
            "post_workable_note",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()

    publish.assert_called_once()
    assert receipt["type"] == "operation_receipt"
    jobs = db.query(BackgroundJobRun).filter(BackgroundJobRun.dispatch_key.isnot(None)).all()
    assert len(jobs) == 1
    assert int(receipt["result"]["job_run_id"]) == int(jobs[0].id)
    command = db.query(ChatCommandReceipt).one()
    assert command.status == "completed"
    assert arguments["body"] not in json.dumps(command.result)


def test_manual_run_crash_replay_propagates_same_paid_cycle_key(db):
    user, role, conversation = _world(db)
    role.agentic_mode_enabled = True
    db.commit()
    preview = dispatch_tool(
        "run_agent_now",
        {},
        db=db,
        role=role,
        user=user,
        conversation=conversation,
    )
    _persist_tool_result(db, conversation=conversation, body=preview)
    _persist_confirmation(db, conversation=conversation, user=user)
    db.commit()

    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        return_value=SimpleNamespace(id="manual-replay"),
    ) as delay:
        with patch(
            "app.agent_chat.tools.complete_command",
            side_effect=RuntimeError("worker killed before receipt"),
        ):
            with pytest.raises(RuntimeError, match="worker killed"):
                dispatch_tool(
                    "run_agent_now",
                    {},
                    db=db,
                    role=role,
                    user=user,
                    conversation=conversation,
                )
        db.rollback()
        receipt = dispatch_tool(
            "run_agent_now",
            {},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()

    assert receipt["status"] == "queued"
    assert receipt["result"]["queued"] is False
    assert receipt["result"]["broker_accepted"] is None
    assert receipt["result"]["dispatch_pending"] is True
    assert receipt["result"]["intent_persisted"] is True
    assert receipt["result"]["replayed"] is True
    assert "automatic queue recovery" in receipt["message"].lower()
    assert "is queued" not in receipt["message"].lower()
    # The replay reuses the same durable intent but its two-minute dispatch
    # reservation prevents a second broker delivery from flooding the queue.
    assert delay.call_count == 1
    keys = [call.kwargs["dispatch_key"] for call in delay.call_args_list]
    assert keys[0].startswith("chat-command/")
    assert db.query(ChatCommandReceipt).count() == 1


def test_disabled_manual_run_is_reported_blocked_without_a_queue_claim(db):
    user, role, conversation = _world(db)
    db.commit()

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        result = dispatch_tool(
            "run_agent_now",
            {},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert result["type"] == "operation_blocked"
    assert result["operation"] == "run_agent_now"
    assert result["preview"]["agent_enabled"] is False
    assert result["preview"]["can_queue"] is False
    assert result["preview"]["blocked_reason"] == (
        "agent is not enabled for this role"
    )
    assert "blocked" in result["message"].lower()
    assert "queued" not in result["message"].lower()
    delay.assert_not_called()


def test_confirmed_role_rescreen_replay_uses_completed_command_receipt(db):
    user, role, conversation = _world(db)
    preview = dispatch_tool(
        "add_or_update_constraint",
        {"text": "Must know Rust", "bucket": "must"},
        db=db,
        role=role,
        user=user,
        conversation=conversation,
    )
    _persist_tool_result(db, conversation=conversation, body=preview)
    _persist_confirmation(db, conversation=conversation, user=user)
    db.commit()

    with patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale",
        return_value=3,
    ) as stale, patch(
        "app.tasks.scoring_tasks.sweep_stale_scores"
    ) as sweep, patch(
        "app.tasks.agent_chat_tasks.report_rescreen_impact"
    ):
        first = dispatch_tool(
            "rescreen_role",
            {},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()
        replay = dispatch_tool(
            "rescreen_role",
            {},
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()

    assert first == replay
    assert first["rescreening_count"] == 3
    stale.assert_called_once()
    sweep.apply_async.assert_called_once()
    receipt = db.query(ChatCommandReceipt).one()
    assert receipt.operation == "rescreen_role"
    assert receipt.status == "completed"


def test_confirmed_scoped_rescreen_replay_uses_completed_command_receipt(db):
    user, role, conversation = _world(db)
    arguments = {"criterion_id": 42, "statuses": ["missing"]}
    affected = [{"application_id": 101}, {"application_id": 102}]
    with patch(
        "app.agent_chat.tools._assessments.affected_applications",
        return_value=affected,
    ):
        preview = dispatch_tool(
            "rescreen_scoped",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
    _persist_tool_result(db, conversation=conversation, body=preview)
    _persist_confirmation(db, conversation=conversation, user=user)
    db.commit()

    with patch(
        "app.agent_chat.tools._assessments.affected_applications",
        return_value=affected,
    ), patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale",
        return_value=2,
    ) as stale, patch(
        "app.tasks.scoring_tasks.sweep_stale_scores"
    ) as sweep, patch(
        "app.tasks.agent_chat_tasks.report_rescreen_impact"
    ):
        first = dispatch_tool(
            "rescreen_scoped",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()
        replay = dispatch_tool(
            "rescreen_scoped",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        db.commit()

    assert first == replay
    assert first["rescreening_count"] == 2
    stale.assert_called_once()
    sweep.apply_async.assert_called_once()
    receipt = db.query(ChatCommandReceipt).one()
    assert receipt.operation == "rescreen_scoped"
    assert receipt.status == "completed"


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


def test_tool_exception_is_not_persisted_or_replayed_verbatim(db):
    user, role, conversation = _world(db)
    organization = db.get(Organization, int(role.organization_id))
    persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Show the current role state.",
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
        [SimpleNamespace(type="tool_use", id="state", name="get_agent_state", input={})],
        "tool_use",
    )
    final_round = response(
        [SimpleNamespace(type="text", text="I couldn't read that state.")],
        "end_turn",
    )
    secret = "postgresql://user:password@private.internal/tenant"

    with (
        patch("app.agent_chat.engine.get_client_for_org", return_value=object()),
        patch("app.agent_chat.engine.reserve"),
        patch("app.agent_chat.engine.one_call", side_effect=[tool_round, final_round]),
        patch("app.agent_chat.engine.dispatch_tool", side_effect=RuntimeError(secret)),
    ):
        run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
        )

    tool_result = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_TOOL,
            AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
        )
        .order_by(AgentConversationMessage.id.desc())
        .first()
    )
    persisted = json.dumps(tool_result.content)
    assert "tool_execution_failed" in persisted
    assert secret not in persisted
