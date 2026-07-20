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
from fastapi import HTTPException

from app.components.assessments.repository import bind_candidate_session
from app.domains.assessments_runtime.candidate_auth import require_candidate_request_proof
from app.main import app
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task
from app.models.user import User

CHAT_SESSION_KEY = "C" * 43


@pytest.fixture(autouse=True)
def _isolate_chat_behavior_from_request_proof():
    """These tests target chat orchestration; PoP has its own route tests."""
    app.dependency_overrides[require_candidate_request_proof] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_candidate_request_proof, None)


def _candidate_headers(token: str = "chat-test-tok") -> dict[str, str]:
    return {
        "X-Assessment-Token": token,
        "X-Assessment-Session": CHAT_SESSION_KEY,
    }


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
    bind_candidate_session(a, CHAT_SESSION_KEY)
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
                headers=_candidate_headers(),
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "Sure — here's what I found."
    assert body["input_tokens"] == 120
    assert body["output_tokens"] == 80
    assert body["request_id"] == "req-test-1"
    assert body["changed_paths"] == []
    assert body["replayed"] is False
    assert "tool_calls_made" not in body

    db.refresh(assessment_in_progress)
    prompts = assessment_in_progress.ai_prompts
    assert len(prompts) == 1
    record = prompts[0]
    assert record["message"] == "Where is the bug in checks.py?"
    assert record["response"] == "Sure — here's what I found."
    assert record["input_tokens"] == 120
    assert record["output_tokens"] == 80
    assert record["request_id"] == "req-test-1"
    assert record["changed_paths"] == []
    assert record["transport"] == "claude_agent_sdk"
    assert len(record["tool_calls_made"]) == 1
    assert assessment_in_progress.total_input_tokens == 120
    assert assessment_in_progress.total_output_tokens == 80


def test_chat_returns_changed_paths_with_current_revisions(
    client, db, assessment_in_progress,
):
    before = {"README.md": "a" * 64, "src/main.py": "b" * 64}
    after = {"README.md": "a" * 64, "src/main.py": "c" * 64}
    turn = _stub_chat_turn(
        content="Updated it.",
        tool_calls=[
            {
                "name": "mcp__sandbox__Edit",
                "input": {"path": "src/main.py", "old": "hi", "new": "fixed"},
                "is_error": False,
            }
        ],
    )
    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as mock_svc_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.workspace_file_revisions",
            side_effect=[before, after],
        ),
        _patch_stack()[0],
        _patch_stack()[1],
        _patch_stack()[2],
        _patch_stack()[3],
        _patch_stack()[4],
        _patch_stack()[5],
    ):
        svc = MagicMock()
        svc.run = AsyncMock(return_value=turn)
        mock_svc_cls.return_value = svc
        response = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "Fix main", "request_id": "changed-paths-1"},
            headers=_candidate_headers(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["changed_paths"] == [
        {"path": "src/main.py", "revision": "c" * 64}
    ]
    db.refresh(assessment_in_progress)
    assert assessment_in_progress.ai_prompts[-1]["changed_paths"] == response.json()["changed_paths"]


def test_chat_request_id_replays_stored_response_without_another_model_call(
    client, db, assessment_in_progress,
):
    changed_paths = [{"path": "src/main.py", "revision": "d" * 64}]
    assessment_in_progress.ai_prompts = [
        {
            "message": "Fix main",
            "response": "Already fixed.",
            "request_id": "retry-safe-1",
            "changed_paths": changed_paths,
            "input_tokens": 12,
            "output_tokens": 7,
            "response_latency_ms": 99,
        }
    ]
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.E2BService"
        ) as e2b_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as service_cls,
    ):
        response = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "Fix main", "request_id": "retry-safe-1"},
            headers=_candidate_headers(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "Already fixed."
    assert response.json()["changed_paths"] == changed_paths
    assert response.json()["replayed"] is True
    e2b_cls.assert_not_called()
    service_cls.assert_not_called()
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 1

    conflict = client.post(
        f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
        json={"message": "Different request", "request_id": "retry-safe-1"},
        headers=_candidate_headers(),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CLAUDE_REQUEST_ID_CONFLICT"


def test_chat_terminal_failure_is_persisted_and_replayed_without_repeating_mutations(
    client, db, assessment_in_progress,
):
    before = {"src/main.py": "a" * 64}
    after = {"src/main.py": "b" * 64}

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as service_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.workspace_file_revisions",
            side_effect=[before, after],
        ),
        _patch_stack()[0],
        _patch_stack()[1],
        _patch_stack()[2],
        _patch_stack()[3],
        _patch_stack()[4],
        _patch_stack()[5],
    ):
        service_cls.return_value.run = AsyncMock(
            side_effect=RuntimeError("provider stream ended after edit")
        )
        first = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "Fix main", "request_id": "failed-attempt-1"},
            headers=_candidate_headers(),
        )

    assert first.status_code == 502, first.text
    assert first.json()["detail"] == {
        "code": "CLAUDE_ATTEMPT_FAILED",
        "message": (
            "Claude hit a problem. Any workspace changes were kept. "
            "Send again to start a new attempt."
        ),
        "request_id": "failed-attempt-1",
        "changed_paths": [{"path": "src/main.py", "revision": "b" * 64}],
        "replayed": False,
    }
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 1
    failed_record = assessment_in_progress.ai_prompts[0]
    assert failed_record["message"] == "Fix main"
    assert failed_record["response"] == ""
    assert failed_record["request_id"] == "failed-attempt-1"
    assert failed_record["attempt_status"] == "failed"
    assert failed_record["changed_paths"] == [
        {"path": "src/main.py", "revision": "b" * 64}
    ]

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.E2BService"
        ) as e2b_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as replay_service_cls,
    ):
        replay = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "Fix main", "request_id": "failed-attempt-1"},
            headers=_candidate_headers(),
        )

    assert replay.status_code == 502, replay.text
    assert replay.json()["detail"] == {
        **first.json()["detail"],
        "replayed": True,
    }
    e2b_cls.assert_not_called()
    replay_service_cls.assert_not_called()
    db.refresh(assessment_in_progress)
    assert len(assessment_in_progress.ai_prompts) == 1


def test_chat_new_request_id_can_start_after_terminal_failure(
    client, db, assessment_in_progress,
):
    assessment_in_progress.ai_prompts = [
        {
            "message": "Fix main",
            "response": "",
            "request_id": "failed-attempt-old",
            "attempt_status": "failed",
            "changed_paths": [{"path": "src/main.py", "revision": "b" * 64}],
        }
    ]
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as service_cls,
        _patch_stack()[0],
        _patch_stack()[1],
        _patch_stack()[2],
        _patch_stack()[3],
        _patch_stack()[4],
        _patch_stack()[5],
    ):
        service_cls.return_value.run = AsyncMock(
            return_value=_stub_chat_turn(content="New attempt completed.")
        )
        response = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "Fix main", "request_id": "failed-attempt-new"},
            headers=_candidate_headers(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "New attempt completed."
    service_cls.return_value.run.assert_awaited_once()
    assert service_cls.return_value.run.await_args.kwargs["messages"] == [
        {"role": "user", "content": "Fix main"}
    ]


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
                headers=_candidate_headers(),
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
        headers=_candidate_headers("wrong-token"),
    )
    assert resp.status_code == 403


def test_chat_rejects_bulk_repo_snapshot(client, assessment_in_progress):
    resp = client.post(
        f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
        json={
            "message": "Use this snapshot",
            "repo_files": [{"path": "src/main.py", "content": "changed"}],
        },
        headers=_candidate_headers(),
    )
    assert resp.status_code == 400
    assert "Bulk repository replacement" in resp.json()["detail"]


def test_chat_requires_request_id_before_workspace_or_model_work(
    client, assessment_in_progress,
):
    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.E2BService"
        ) as e2b_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.AgentSDKChatService"
        ) as service_cls,
        _patch_stack()[1],
        _patch_stack()[2],
        _patch_stack()[3],
        _patch_stack()[4],
    ):
        response = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "Fix main"},
            headers=_candidate_headers(),
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "CLAUDE_REQUEST_ID_REQUIRED"
    e2b_cls.assert_not_called()
    service_cls.assert_not_called()


def test_chat_rejects_paused_assessment_before_paid_or_sandbox_work(
    client, db, assessment_in_progress,
):
    assessment_in_progress.is_timer_paused = True
    assessment_in_progress.paused_at = datetime.now(timezone.utc)
    assessment_in_progress.pause_reason = "provider_outage"
    db.commit()

    with patch(
        "app.domains.assessments_runtime.candidate_claude_chat_routes.E2BService"
    ) as e2b_cls:
        resp = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "keep working"},
            headers=_candidate_headers(),
        )
    assert resp.status_code == 423
    assert resp.json()["detail"]["code"] == "ASSESSMENT_PAUSED"
    e2b_cls.assert_not_called()


def test_chat_enforces_server_deadline_before_paid_or_sandbox_work(
    client, db, assessment_in_progress,
):
    assessment_in_progress.started_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    db.commit()

    with (
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.E2BService"
        ) as e2b_cls,
        patch(
            "app.domains.assessments_runtime.candidate_claude_chat_routes.enforce_active_or_timeout",
            side_effect=HTTPException(status_code=409, detail="Assessment time expired and was auto-submitted"),
        ),
    ):
        resp = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "keep working"},
            headers=_candidate_headers(),
        )
    assert resp.status_code == 409
    e2b_cls.assert_not_called()


def test_chat_returns_409_when_workspace_not_active(client, db, assessment_in_progress):
    assessment_in_progress.e2b_session_id = None
    db.commit()
    with _patch_stack()[1], _patch_stack()[2], _patch_stack()[3], _patch_stack()[4]:
        resp = client.post(
            f"/api/v1/assessments/{assessment_in_progress.id}/claude/chat",
            json={"message": "hi"},
            headers=_candidate_headers(),
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
                headers=_candidate_headers(),
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
                    "request_id": "editor-context-1",
                    "code_context": "def f():\n    return 1/0",
                    "selected_file_path": "src/main.py",
                },
                headers=_candidate_headers(),
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
                headers=_candidate_headers(),
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
                json={"message": "should not spend", "request_id": "credit-gate-1"},
                headers=_candidate_headers(),
            )

    assert resp.status_code == 402
    mock_svc_cls.assert_not_called()
