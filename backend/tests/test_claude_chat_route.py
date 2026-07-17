"""Integration tests for the new agentic ``POST /assessments/:id/claude/chat`` route.

Mocks the E2B sandbox + the underlying Anthropic SDK so we exercise the route
logic (auth, sandbox connect, prior-prompt flattening, ai_prompts persistence,
budget gating, response shape) without touching real services.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User
from app.components.integrations.claude_agent.types import ChatTurn


@pytest.fixture
def assessment_in_progress(db):
    org = Organization(
        name="Chat Test Org",
        slug=f"chat-org-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=f"recruiter-{id(db)}@x.test",
        full_name="Recruiter",
        is_verified=True,
        hashed_password="x",
    )
    db.add(user)
    candidate = Candidate(
        organization_id=org.id, email=f"cand-{id(db)}@x.test", full_name="Cand",
    )
    db.add(candidate)
    db.flush()
    task = Task(
        name="Sample task",
        task_key="chat-route-sample",
        organization_id=org.id,
        scenario="You're recovering a data quality framework.",
        repo_structure={
            "name": "chat-route-sample-repo",
            "files": {"README.md": "# Hi", "src/main.py": "print('hi')"},
        },
        is_active=True,
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    a = Assessment(
        organization_id=org.id,
        candidate_id=candidate.id,
        task_id=task.id,
        token="chat-test-tok",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        duration_minutes=30,
        e2b_session_id="sbx-12345",
        ai_prompts=[],
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _stub_chat_turn(
    content="Sure — here's what I found.",
    *,
    input_tokens=120,
    output_tokens=80,
    tool_calls=None,
    success=True,
    stop_reason="end_turn",
):
    return SimpleNamespace(
        success=success,
        stop_reason=stop_reason,
        role="assistant",
        content=content,
        tool_calls_made=tool_calls or [
            {"name": "read_file", "input": {"path": "src/main.py"}, "result_ok": True},
        ],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _patch_stack():
    """Patch every external dependency the route reaches into. Returns the patch list."""
    return [
        # Sandbox connect — return a non-falsy stub.
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.E2BService",
            return_value=MagicMock(
                connect_sandbox=MagicMock(return_value=MagicMock()),
            ),
        ),
        # API key lookup — server-side accessor (agentic-only: the key never
        # enters the sandbox). Return something non-empty.
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.resolve_backend_anthropic_key",
            return_value="sk-test",
        ),
        # Role budget gate — allow spend.
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.can_spend_on_role",
            return_value=True,
        ),
        # Budget snapshot helper — return a stub with remaining_usd present.
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.build_claude_budget_snapshot",
            return_value={"enabled": True, "remaining_usd": 4.5, "spent_usd": 0.5, "limit_usd": 5.0},
        ),
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.resolve_effective_budget_limit_usd",
            return_value=5.0,
        ),
        # AssessmentToolExecutor — stub it; the chat service is mocked anyway.
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AssessmentToolExecutor",
            return_value=MagicMock(),
        ),
    ]


def test_chat_happy_path_persists_one_ai_prompts_record(client, db, assessment_in_progress):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={
                    "message": "Where is the bug in checks.py?",
                    "request_id": "req-test-1",
                    "paste_detected": False,
                    "browser_focused": True,
                },
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "Sure — here's what I found."
    assert body["input_tokens"] == 120
    assert body["output_tokens"] == 80
    assert body["request_id"] == "req-test-1"
    assert isinstance(body["tool_calls_made"], list) and len(body["tool_calls_made"]) == 1

    db.refresh(assessment_in_progress)
    prompts = assessment_in_progress.ai_prompts
    assert len(prompts) == 1
    record = prompts[0]
    assert record["message"] == "Where is the bug in checks.py?"
    assert record["response"] == "Sure — here's what I found."
    assert record["input_tokens"] == 120
    assert record["output_tokens"] == 80
    assert record["transport"] == "claude_agent_sdk"
    assert record["request_id"] == "req-test-1"
    assert len(record["tool_calls_made"]) == 1
    assert assessment_in_progress.total_input_tokens == 120
    assert assessment_in_progress.total_output_tokens == 80
    claim = assessment_in_progress.prompt_analytics["_candidate_chat_requests_v1"][
        "req-test-1"
    ]
    assert claim["state"] == "completed"
    assert "chat_turn_checkpoint" not in claim
    assert "finalization_input" not in claim


def test_chat_replays_duplicate_request_without_second_paid_call(
    client, db, assessment_in_progress,
):
    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.reserve"
        ) as reserve_mock,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="one paid answer"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            payload = {"message": "check this", "request_id": "stable-retry-1"}
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            second = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["content"] == "one paid answer"
    assert second.json()["idempotent_replay"] is True
    svc.run.assert_awaited_once()
    reserve_mock.assert_called_once()
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 1
    assert assessment_in_progress.total_input_tokens == 120


def test_chat_replays_committed_response_after_assessment_becomes_inactive(
    client, db, assessment_in_progress,
):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="committed before void"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            payload = {"message": "exact replay", "request_id": "inactive-replay-1"}
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            assessment_in_progress.is_voided = True
            assessment_in_progress.e2b_session_id = None
            db.commit()
            replay = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["content"] == "committed before void"
    assert replay.json()["idempotent_replay"] is True
    svc.run.assert_awaited_once()


def test_chat_resumes_db_finalization_from_checkpoint_without_second_paid_call(
    client, db, assessment_in_progress,
):
    from app.components.assessments import candidate_chat_runtime

    original_finalize = candidate_chat_runtime.finalize_candidate_chat_turn
    finalize_calls = 0

    def fail_finalization_once(**kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise HTTPException(status_code=503, detail="temporary database failure")
        return original_finalize(**kwargs)

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.components.assessments.candidate_chat_runtime.finalize_candidate_chat_turn",
            side_effect=fail_finalization_once,
        ),
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.reserve"
        ) as reserve_mock,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="checkpointed answer"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            payload = {"message": "recover finalization", "request_id": "checkpoint-1"}
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            retry = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )

    assert first.status_code == 503, first.text
    assert retry.status_code == 200, retry.text
    assert retry.json()["content"] == "checkpointed answer"
    svc.run.assert_awaited_once()
    reserve_mock.assert_called_once()
    assert finalize_calls == 2
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 1
    assert assessment_in_progress.ai_prompts[0]["request_id"] == "checkpoint-1"
    claim = assessment_in_progress.prompt_analytics["_candidate_chat_requests_v1"][
        "checkpoint-1"
    ]
    assert claim["state"] == "completed"


def test_chat_rejects_request_id_reuse_for_different_message(
    client, db, assessment_in_progress,
):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "first", "request_id": "reused-id"},
                headers=headers,
            )
            conflict = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "different", "request_id": "reused-id"},
                headers=headers,
            )

    assert first.status_code == 200, first.text
    assert conflict.status_code == 409, conflict.text
    svc.run.assert_awaited_once()


def test_chat_flattens_prior_prompts_to_messages(client, db, assessment_in_progress):
    assessment_in_progress.ai_prompts = [
        {"message": "first", "response": "ok"},
        {"message": "second", "response": "fine"},
    ]
    db.commit()
    db.refresh(assessment_in_progress)

    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="third reply"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "third", "request_id": "r3"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 200
    # The service.run call should have received 5 messages: 2 user/assistant
    # pairs from prior history + the new user message.
    called_messages = svc.run.call_args.kwargs["messages"]
    assert [m["role"] for m in called_messages] == ["user", "assistant", "user", "assistant", "user"]
    assert called_messages[-1]["content"] == "third"
    # The new turn is appended as one record.
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 3
    assert assessment_in_progress.ai_prompts[-1]["response"] == "third reply"


def test_chat_rejects_invalid_token(client, db, assessment_in_progress):
    resp = client.post(
        f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
        json={"message": "hi", "request_id": "invalid-token"},
        headers={"X-Assessment-Token": "wrong-token"},
    )
    assert resp.status_code == 403


def test_chat_returns_409_when_workspace_not_active(client, db, assessment_in_progress):
    assessment_in_progress.e2b_session_id = None
    db.commit()
    with _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4]:
        resp = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "hi", "request_id": "workspace-inactive"},
            headers={"X-Assessment-Token": "chat-test-tok"},
        )
    assert resp.status_code == 409


def test_chat_returns_402_when_role_budget_exhausted(client, db, assessment_in_progress):
    with _patch_stack()[1], _patch_stack()[3], _patch_stack()[4]:
        with patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.can_spend_on_role",
            return_value=False,
        ):
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "hi", "request_id": "role-budget-empty"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )
    assert resp.status_code == 402


def test_chat_embeds_editor_context_when_provided(client, db, assessment_in_progress):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={
                    "message": "Why does this fail?",
                    "code_context": "def f():\n    return 1/0",
                    "selected_file_path": "src/main.py",
                    "request_id": "editor-context-1",
                },
                headers={"X-Assessment-Token": "chat-test-tok"},
            )
    assert resp.status_code == 200
    called_messages = svc.run.call_args.kwargs["messages"]
    last_user = called_messages[-1]["content"]
    assert "Why does this fail?" in last_user
    assert "<editor_context" in last_user
    assert "src/main.py" in last_user
    assert "1/0" in last_user


def test_chat_classifier_exception_carries_state_forward_and_does_not_block_agent(
    client, db, assessment_in_progress,
):
    task = db.query(Task).filter(Task.id == assessment_in_progress.task_id).one()
    task.extra_data = {
        "decision_points": [
            {
                "id": "storage",
                "headline": "Storage boundary",
                "ask": "Which storage boundary will you use?",
            }
        ]
    }
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.classify_response",
            side_effect=RuntimeError("classifier transport failed"),
        ) as classifier_mock,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="We can continue safely."))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "Use a repository layer", "request_id": "classifier-fallback-1"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "We can continue safely."
    classifier_mock.assert_called_once()
    svc.run.assert_awaited_once()
    db.refresh(assessment_in_progress)
    record = assessment_in_progress.ai_prompts[0]
    assert record["interrogation_state"]["storage"]["status"] == "unaddressed"
    claim = assessment_in_progress.prompt_analytics["_candidate_chat_requests_v1"][
        "classifier-fallback-1"
    ]
    assert claim["state"] == "completed"
    assert "classifier_error" not in claim
    assert "chat_turn_checkpoint" not in claim


def test_chat_runs_sync_classifier_off_the_async_route_event_loop(
    client, db, assessment_in_progress,
):
    task = db.query(Task).filter(Task.id == assessment_in_progress.task_id).one()
    task.extra_data = {
        "decision_points": [
            {
                "id": "storage",
                "headline": "Storage boundary",
                "ask": "Which storage boundary will you use?",
            }
        ]
    }
    db.commit()
    classifier_threads = []
    agent_threads = []

    def classify(**_kwargs):
        classifier_threads.append(threading.get_ident())
        return SimpleNamespace(by_dp={}, error=None)

    async def run_agent(**_kwargs):
        agent_threads.append(threading.get_ident())
        return _stub_chat_turn(content="Event loop remained responsive.")

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.classify_response",
            side_effect=classify,
        ),
    ):
        svc = MagicMock()
        svc.run = AsyncMock(side_effect=run_agent)
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            response = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={
                    "message": "Use a repository layer",
                    "request_id": "classifier-thread-1",
                },
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert response.status_code == 200, response.text
    assert len(classifier_threads) == len(agent_threads) == 1
    assert classifier_threads[0] != agent_threads[0]


def test_chat_agent_wall_timeout_is_ambiguous_and_never_replayed(
    client, db, assessment_in_progress,
):
    async def never_resolves(**_kwargs):
        await asyncio.Event().wait()

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.components.assessments.candidate_chat_runtime._AGENT_CHAT_WALL_TIMEOUT_SECONDS",
            0.01,
        ),
    ):
        svc = MagicMock()
        svc.run = AsyncMock(side_effect=never_resolves)
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            payload = {
                "message": "A provider turn that hangs",
                "request_id": "agent-wall-timeout-1",
            }
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            exact_retry = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )

    assert first.status_code == 504, first.text
    assert "will not be replayed" in first.json()["detail"]["message"]
    assert exact_retry.status_code == 409, exact_retry.text
    svc.run.assert_awaited_once()
    db.expire_all()
    stored = db.get(Assessment, assessment_in_progress.id)
    claim = stored.prompt_analytics["_candidate_chat_requests_v1"][
        "agent-wall-timeout-1"
    ]
    assert claim["state"] == "manual_reconciliation_required"
    assert claim["provider_disposition"] == "manual_reconciliation_required"
    assert claim["reconciliation_disposition"] == "provider_outcome_not_replayed"
    assert claim["last_error"] == "agent_call_timed_out"


def test_chat_marks_first_candidate_prompt_after_seeded_opener(
    client, db, assessment_in_progress,
):
    assessment_in_progress.ai_prompts = [
        {"message": "", "response": "Welcome — choose your approach."}
    ]
    db.commit()

    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "My first answer", "request_id": "first-after-opener"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 200, resp.text
    db.refresh(assessment_in_progress)
    event_types = [event.get("event_type") for event in assessment_in_progress.timeline or []]
    assert event_types.count("first_prompt") == 1


def test_chat_reserves_call_and_threads_stable_role_trace(
    client, db, assessment_in_progress,
):
    role = Role(
        organization_id=assessment_in_progress.organization_id,
        name="Trace role",
    )
    db.add(role)
    db.flush()
    assessment_in_progress.role_id = role.id
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.reserve"
        ) as reserve_mock,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "trace this", "request_id": "stable-req-7"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 200, resp.text
    reserve_mock.assert_called_once()
    init = mock_svc_cls.call_args.kwargs
    assert init["role_id"] == role.id
    assert init["trace_id"] == (
        f"assessment:{assessment_in_progress.id}:chat:stable-req-7:agent"
    )


def test_chat_credit_gate_blocks_before_paid_sdk_call(
    client, db, assessment_in_progress,
):
    from app.services.usage_metering_service import InsufficientCreditsError

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.reserve",
            side_effect=InsufficientCreditsError(
                organization_id=int(assessment_in_progress.organization_id),
                required=60_000,
                available=0,
            ),
        ),
    ):
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={
                    "message": "should not spend",
                    "request_id": "credit-gate-1",
                },
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 402
    mock_svc_cls.assert_not_called()


def test_chat_provider_calls_run_without_request_transaction(
    client, db, assessment_in_progress,
):
    from app.components.assessments import candidate_chat_runtime

    observed = []
    original_assertion = candidate_chat_runtime._assert_provider_detached

    def checked_assertion(request_db, phase):
        observed.append((phase, request_db.in_transaction()))
        original_assertion(request_db, phase)

    with (
        patch(
            "app.components.assessments.candidate_chat_runtime._assert_provider_detached",
            checked_assertion,
        ),
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            resp = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "detached", "request_id": "detached-1"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 200, resp.text
    assert ("E2B connect", False) in observed
    assert ("Agent SDK", False) in observed


def test_chat_exact_hash_rejects_changed_context_for_same_request_id(
    client, assessment_in_progress,
):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn())
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "same", "code_context": "a", "request_id": "exact-1"},
                headers=headers,
            )
            conflict = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "same", "code_context": "b", "request_id": "exact-1"},
                headers=headers,
            )

    assert first.status_code == 200
    assert conflict.status_code == 409
    svc.run.assert_awaited_once()


def test_chat_ambiguous_agent_failure_is_not_replayed_or_resent(
    client, assessment_in_progress,
):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(side_effect=RuntimeError("provider connection dropped"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            payload = {"message": "ambiguous", "request_id": "ambiguous-1"}
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            retry = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )

    assert first.status_code == 502
    assert "start a new request" in first.json()["detail"]["message"]
    assert retry.status_code == 409
    svc.run.assert_awaited_once()


def test_false_chat_turn_budget_failure_is_retryable_and_never_committed(
    client, db, assessment_in_progress,
):
    turn = ChatTurn(
        success=False,
        content="Budget exhausted",
        stop_reason="budget_exhausted",
    )
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=turn)
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            response = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "help", "request_id": "false-budget-1"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert response.status_code == 402, response.text
    db.refresh(assessment_in_progress)
    assert assessment_in_progress.ai_prompts == []
    claim = assessment_in_progress.prompt_analytics["_candidate_chat_requests_v1"][
        "false-budget-1"
    ]
    assert claim["state"] == "classifier_completed"
    assert claim["provider_disposition"] == (
        "definite_pre_provider_budget_rejection"
    )
    assert claim["chat_turn_checkpoint"]["success"] is False
    assert claim["chat_turn_checkpoint"]["stop_reason"] == "budget_exhausted"


def test_false_post_start_turn_fences_exact_retry_but_allows_distinct_request(
    client, db, assessment_in_progress,
):
    failed = ChatTurn(
        success=False,
        content="Partial provider output",
        input_tokens=10,
        output_tokens=2,
        stop_reason="no_result_message",
    )
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(
            side_effect=[failed, _stub_chat_turn(content="A later safe answer")]
        )
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            original = {
                "message": "ambiguous false turn",
                "request_id": "false-ambiguous-1",
            }
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=original,
                headers=headers,
            )
            exact_retry = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=original,
                headers=headers,
            )
            later = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "a distinct follow-up", "request_id": "later-2"},
                headers=headers,
            )
            closed_exact_retry = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=original,
                headers=headers,
            )

    assert first.status_code == 502, first.text
    assert exact_retry.status_code == 409, exact_retry.text
    assert later.status_code == 200, later.text
    assert closed_exact_retry.status_code == 409, closed_exact_retry.text
    assert later.json()["content"] == "A later safe answer"
    assert svc.run.await_count == 2
    db.refresh(assessment_in_progress)
    old_claim = assessment_in_progress.prompt_analytics[
        "_candidate_chat_requests_v1"
    ]["false-ambiguous-1"]
    assert old_claim["state"] == "reconciled_no_replay"
    assert old_claim["reconciliation_original_state"] == (
        "manual_reconciliation_required"
    )
    assert old_claim["reconciliation_disposition"] == (
        "provider_outcome_not_replayed"
    )
    assert len(assessment_in_progress.ai_prompts) == 1
    reconciliation_events = [
        event
        for event in assessment_in_progress.timeline
        if event.get("event_type", "").startswith("candidate_chat_reconciliation")
        or event.get("event_type") == "candidate_chat_reconciled_no_replay"
    ]
    assert [event["event_type"] for event in reconciliation_events] == [
        "candidate_chat_reconciliation_required",
        "candidate_chat_reconciled_no_replay",
    ]
    assert reconciliation_events[-1]["disposition"] == (
        "provider_outcome_not_replayed"
    )
    assert "ambiguous false turn" not in str(reconciliation_events)
    from app.components.assessments.candidate_chat_submission import (
        finalize_or_block_candidate_chat_for_submit,
    )

    assert (
        finalize_or_block_candidate_chat_for_submit(
            db,
            assessment_id=assessment_in_progress.id,
            token="chat-test-tok",
        )
        is False
    )


def test_chat_requires_request_id_before_any_new_provider_work(
    client, assessment_in_progress,
):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            response = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "missing stable identity"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert response.status_code == 422
    mock_svc_cls.assert_not_called()


def test_checkpoint_recovers_on_new_request_id_and_aliases_without_respend(
    client, db, assessment_in_progress,
):
    from app.components.assessments import candidate_chat_runtime

    original_finalize = candidate_chat_runtime.finalize_candidate_chat_turn
    finalize_calls = 0

    def fail_once(**kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise HTTPException(status_code=503, detail="lost response after checkpoint")
        return original_finalize(**kwargs)

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.components.assessments.candidate_chat_runtime.finalize_candidate_chat_turn",
            side_effect=fail_once,
        ),
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="durable answer"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "same payload", "request_id": "lost-old"},
                headers=headers,
            )
            recovered = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "same payload", "request_id": "lost-new"},
                headers=headers,
            )

    assert first.status_code == 503, first.text
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["content"] == "durable answer"
    assert recovered.json()["request_id"] == "lost-new"
    assert recovered.json()["idempotent_replay"] is True
    svc.run.assert_awaited_once()
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 1
    aliases = assessment_in_progress.ai_prompts[0]["request_aliases"]
    assert aliases[0]["request_id"] == "lost-new"
    old_claim = assessment_in_progress.prompt_analytics[
        "_candidate_chat_requests_v1"
    ]["lost-old"]
    assert old_claim["state"] == "completed"
    assert "chat_turn_checkpoint" not in old_claim


def test_committed_replay_precedes_expiry_but_new_spend_is_blocked(
    client, db, assessment_in_progress,
):
    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.finalize_timed_out_assessment",
            return_value={"status": "finalized"},
        ) as timeout_finalize,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="before expiry"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            payload = {"message": "remember me", "request_id": "expiry-replay"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            assessment_in_progress.started_at = datetime.now(timezone.utc) - timedelta(
                minutes=31
            )
            assessment_in_progress.duration_minutes = 30
            db.commit()
            replay = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            blocked = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "new spend", "request_id": "expired-new"},
                headers=headers,
            )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert blocked.status_code == 409, blocked.text
    svc.run.assert_awaited_once()
    timeout_finalize.assert_called_once()


def test_expired_chat_reports_reconciliation_when_timeout_cannot_finalize(
    client, db, assessment_in_progress,
):
    assessment_in_progress.started_at = datetime.now(timezone.utc) - timedelta(
        minutes=31
    )
    assessment_in_progress.duration_minutes = 30
    db.commit()
    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as provider,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.finalize_timed_out_assessment",
            return_value={
                "status": "blocked",
                "reason": "chat_reconciliation_required",
                "assessment_id": assessment_in_progress.id,
            },
        ) as timeout_finalize,
    ):
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            response = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "expired", "request_id": "expired-reconcile"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == (
        "ASSESSMENT_TIMEOUT_RECONCILIATION_REQUIRED"
    )
    assert "auto-submitted" not in response.text
    timeout_finalize.assert_called_once()
    provider.assert_not_called()


def test_committed_replay_precedes_pause_but_new_spend_is_locked(
    client, db, assessment_in_progress,
):
    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
    ) as mock_svc_cls:
        svc = MagicMock()
        svc.run = AsyncMock(return_value=_stub_chat_turn(content="before pause"))
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            payload = {"message": "remember pause", "request_id": "pause-replay"}
            first = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            assessment_in_progress.is_timer_paused = True
            assessment_in_progress.paused_at = datetime.now(timezone.utc)
            assessment_in_progress.pause_reason = "provider maintenance"
            db.commit()
            replay = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json=payload,
                headers=headers,
            )
            blocked = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "new paused spend", "request_id": "pause-new"},
                headers=headers,
            )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert blocked.status_code == 423, blocked.text
    svc.run.assert_awaited_once()


def test_submit_finalizes_checkpoint_then_syncs_detached_under_one_mutex(
    client, db, assessment_in_progress,
):
    from app.components.assessments import candidate_chat_runtime

    original_finalize = candidate_chat_runtime.finalize_candidate_chat_turn
    finalize_calls = 0
    mutex_events: list[str] = []

    def fail_once(**kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise HTTPException(status_code=503, detail="checkpoint before submit")
        return original_finalize(**kwargs)

    @contextmanager
    def observed_mutex(_db, *, assessment_id):
        assert assessment_id == assessment_in_progress.id
        mutex_events.append("entered")
        try:
            yield
        finally:
            mutex_events.append("exited")

    def connect_detached(_e2b, assessment, _task, request_db, *, persist=True):
        assert mutex_events == ["entered"]
        assert persist is False
        assert request_db.in_transaction() is False
        return MagicMock(), "/workspace/repo"

    def sync_detached(_sandbox, _repo_root, files):
        assert mutex_events == ["entered"]
        assert files == {"src/main.py": "print('submitted')"}

    def submit_without_nested_lock(
        assessment,
        _final_code,
        _tab_switch_count,
        request_db,
        *,
        workspace_lock_held=False,
    ):
        assert mutex_events == ["entered"]
        assert workspace_lock_held is True
        assert request_db.in_transaction()
        assert assessment.ai_prompts[0]["response"] == "checkpoint before submit"
        claim = assessment.prompt_analytics["_candidate_chat_requests_v1"][
            "checkpoint-submit-1"
        ]
        assert claim["state"] == "completed"
        assert "chat_turn_checkpoint" not in claim
        request_db.rollback()
        return {"success": True, "grading_status": "complete"}

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.components.assessments.candidate_chat_runtime.finalize_candidate_chat_turn",
            side_effect=fail_once,
        ),
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes.assessment_workspace_mutex",
            observed_mutex,
        ),
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes.build_sandbox_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes._connect_assessment_sandbox",
            side_effect=connect_detached,
        ),
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes._sync_repo_files_to_sandbox",
            side_effect=sync_detached,
        ),
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes._submit_assessment",
            side_effect=submit_without_nested_lock,
        ),
    ):
        svc = MagicMock()
        svc.run = AsyncMock(
            return_value=_stub_chat_turn(content="checkpoint before submit")
        )
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            chat = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={
                    "message": "finish before submit",
                    "request_id": "checkpoint-submit-1",
                },
                headers=headers,
            )
            submitted = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/submit",
                json={
                    "final_code": "print('submitted')",
                    "repo_files": [
                        {"path": "src/main.py", "content": "print('submitted')"}
                    ],
                },
                headers=headers,
            )

    assert chat.status_code == 503, chat.text
    assert submitted.status_code == 200, submitted.text
    assert mutex_events == ["entered", "exited"]
    svc.run.assert_awaited_once()


def test_timeout_finalizes_checkpoint_before_canonical_workspace_grading(
    client, db, assessment_in_progress,
):
    from app.components.assessments import candidate_chat_runtime
    from app.components.assessments import service as assessment_service

    original_finalize = candidate_chat_runtime.finalize_candidate_chat_turn
    finalize_calls = 0

    def checkpoint_once(**kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise HTTPException(status_code=503, detail="checkpoint before timeout")
        return original_finalize(**kwargs)

    def grade_current_workspace(
        assessment,
        _final_code,
        _tab_switch_count,
        request_db,
        *,
        wake_agent_on_commit=True,
        enqueue_rubric_retry_on_commit=True,
        workspace_lock_held=False,
    ):
        assert wake_agent_on_commit is False
        assert enqueue_rubric_retry_on_commit is False
        assert workspace_lock_held is True
        assert assessment.ai_prompts[0]["response"] == "checkpoint at timeout"
        claim = assessment.prompt_analytics["_candidate_chat_requests_v1"][
            "checkpoint-timeout-1"
        ]
        assert claim["state"] == "completed"
        assert "chat_turn_checkpoint" not in claim
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)
        request_db.commit()
        return {"success": True}

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.components.assessments.candidate_chat_runtime.finalize_candidate_chat_turn",
            side_effect=checkpoint_once,
        ),
        patch(
            "app.components.assessments.service.submit_assessment",
            side_effect=grade_current_workspace,
        ),
        patch(
            "app.components.assessments.candidate_chat_submission.build_claude_budget_snapshot",
            return_value={
                "enabled": True,
                "remaining_usd": 4.5,
                "spent_usd": 0.5,
                "limit_usd": 5.0,
            },
        ),
        patch(
            "app.components.assessments.candidate_chat_submission.resolve_effective_budget_limit_usd",
            return_value=5.0,
        ),
    ):
        svc = MagicMock()
        svc.run = AsyncMock(
            return_value=_stub_chat_turn(content="checkpoint at timeout")
        )
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            chat = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={
                    "message": "finish at timeout",
                    "request_id": "checkpoint-timeout-1",
                },
                headers={"X-Assessment-Token": "chat-test-tok"},
            )
        assert chat.status_code == 503, chat.text
        assessment_in_progress.started_at = datetime.now(timezone.utc) - timedelta(
            minutes=31
        )
        assessment_in_progress.duration_minutes = 30
        db.commit()

        result = assessment_service.finalize_timed_out_assessment(
            assessment_in_progress,
            db,
        )

    assert result["status"] == "finalized"
    svc.run.assert_awaited_once()
    db.expire_all()
    stored = db.get(Assessment, assessment_in_progress.id)
    assert stored.status == AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
    assert len(stored.ai_prompts) == 1


def test_submit_blocks_unresolved_chat_before_workspace_provider(
    client, assessment_in_progress,
):
    failed = ChatTurn(
        success=False,
        content="ambiguous",
        stop_reason="no_result_message",
    )
    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes._connect_assessment_sandbox"
        ) as connect_mock,
        patch(
            "app.domains.assessments_runtime.candidate_runtime_routes._submit_assessment"
        ) as submit_mock,
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=failed)
        mock_svc_cls.return_value = svc
        with _patch_stack()[0], _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4], _patch_stack()[5]:
            headers = {"X-Assessment-Token": "chat-test-tok"}
            chat = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
                json={"message": "ambiguous", "request_id": "submit-blocked-chat"},
                headers=headers,
            )
            submitted = client.post(
                f"/api/v1/assessments/{assessment_in_progress.id}/submit",
                json={"final_code": "print('do not grade yet')"},
                headers=headers,
            )

    assert chat.status_code == 502, chat.text
    assert submitted.status_code == 409, submitted.text
    connect_mock.assert_not_called()
    submit_mock.assert_not_called()
