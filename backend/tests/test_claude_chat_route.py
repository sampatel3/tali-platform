"""Integration tests for the new agentic ``POST /assessments/:id/claude/chat`` route.

Mocks the E2B sandbox + the underlying Anthropic SDK so we exercise the route
logic (auth, sandbox connect, prior-prompt flattening, ai_prompts persistence,
budget gating, response shape) without touching real services.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User


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


def _stub_chat_turn(content="Sure — here's what I found.", *, input_tokens=120, output_tokens=80, tool_calls=None):
    return SimpleNamespace(
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
    assert len(record["tool_calls_made"]) == 1
    assert assessment_in_progress.total_input_tokens == 120
    assert assessment_in_progress.total_output_tokens == 80


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
        json={"message": "hi"},
        headers={"X-Assessment-Token": "wrong-token"},
    )
    assert resp.status_code == 403


def test_chat_returns_409_when_workspace_not_active(client, db, assessment_in_progress):
    assessment_in_progress.e2b_session_id = None
    db.commit()
    with _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4]:
        resp = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "hi"},
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
                json={"message": "hi"},
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
                json={"message": "should not spend"},
                headers={"X-Assessment-Token": "chat-test-tok"},
            )

    assert resp.status_code == 402
    mock_svc_cls.assert_not_called()
