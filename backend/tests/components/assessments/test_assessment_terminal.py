import json

from tests.conftest import verify_user
from types import SimpleNamespace


def _register_and_login(client):
    client.post("/api/v1/auth/register", json={
        "email": "terminal@example.com",
        "password": "testpass123",
        "full_name": "Terminal User",
        "organization_name": "Terminal Org",
    })
    verify_user("terminal@example.com")
    login_resp = client.post("/api/v1/auth/jwt/login", data={
        "username": "terminal@example.com",
        "password": "testpass123",
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_task(client, headers):
    payload = {
        "name": "Terminal Task",
        "description": "desc",
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": 30,
        "starter_code": "print('x')",
        "test_code": "def test_ok(): assert True",
        "task_key": "terminal-task",
        "role": "Data Engineer",
        "scenario": "Run terminal-native Claude",
        "repo_structure": {"files": {"src/main.py": "def run():\n    return 1"}},
        "evaluation_rubric": {"correctness": 0.7, "readability": 0.3},
    }
    resp = client.post("/api/v1/tasks", json=payload, headers=headers)
    return resp.json()


def _create_task_with_bootstrap(client, headers):
    payload = {
        "name": "Bootstrapped Terminal Task",
        "description": "desc",
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": 30,
        "starter_code": "print('x')",
        "test_code": "def test_ok(): assert True",
        "task_key": "bootstrapped-terminal-task",
        "role": "Data Engineer",
        "scenario": "Run terminal-native Claude",
        "repo_structure": {
            "name": "bootstrapped-terminal-task",
            "files": {"src/main.py": "def run():\n    return 1", "requirements.txt": "pytest\n"},
        },
        "evaluation_rubric": {"correctness": 0.7, "readability": 0.3},
        "extra_data": {
            "workspace_bootstrap": {
                "commands": ["python3 -m venv .venv", "./.venv/bin/python -m pip install -r requirements.txt"],
                "working_dir": "/workspace/bootstrapped-terminal-task",
                "timeout_seconds": 120,
                "must_succeed": True,
            }
        },
    }
    resp = client.post("/api/v1/tasks", json=payload, headers=headers)
    return resp.json()


def _create_assessment(client, headers, task_id):
    resp = client.post("/api/v1/assessments", json={
        "candidate_email": "candidate-terminal@example.com",
        "candidate_name": "Candidate Terminal",
        "task_id": task_id,
        "duration_minutes": 30,
    }, headers=headers)
    return resp.json()


def test_start_payload_exposes_terminal_mode_contract(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    assessment = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    import app.components.assessments.terminal_runtime as terminal_runtime

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *args, **kwargs: None})()

        def run_code(self, _code):
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("terminal-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            return None

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_clone_assessment_branch_into_workspace", lambda *_args, **_kwargs: True)

    start = client.post(f"/api/v1/assessments/token/{assessment['token']}/start")
    assert start.status_code == 200
    payload = start.json()
    assert payload["ai_mode"] == "claude_cli_terminal"
    assert payload["terminal_mode"] is True
    assert payload["terminal_capabilities"]["enabled"] is True


def test_start_assessment_runs_workspace_bootstrap_and_records_timeline(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task_with_bootstrap(client, headers)
    assessment = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    import app.components.assessments.terminal_runtime as terminal_runtime

    calls = []

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *args, **kwargs: None})()

        def run_code(self, _code):
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("bootstrap-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            return None

        def run_command(self, sandbox, command, cwd=None, timeout=30):
            calls.append({"sandbox": sandbox.sandbox_id, "command": command, "cwd": cwd, "timeout": timeout})
            return {"stdout": "ok", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_clone_assessment_branch_into_workspace", lambda *_args, **_kwargs: True)

    start = client.post(f"/api/v1/assessments/token/{assessment['token']}/start")
    assert start.status_code == 200, start.text
    assert calls == [
        {
            "sandbox": "bootstrap-sandbox",
            "command": "python3 -m venv .venv",
            "cwd": "/workspace/bootstrapped-terminal-task",
            "timeout": 120,
        },
        {
            "sandbox": "bootstrap-sandbox",
            "command": "./.venv/bin/python -m pip install -r requirements.txt",
            "cwd": "/workspace/bootstrapped-terminal-task",
            "timeout": 120,
        },
    ]

    detail = client.get(f"/api/v1/assessments/{assessment['id']}", headers=headers)
    assert detail.status_code == 200
    timeline = detail.json().get("timeline") or []
    bootstrap_event = next(event for event in timeline if event.get("event_type") == "workspace_bootstrap")
    assert bootstrap_event["success"] is True
    assert bootstrap_event["working_dir"] == "/workspace/bootstrapped-terminal-task"
    assert len(bootstrap_event["steps"]) == 2


def test_start_assessment_blocks_when_workspace_bootstrap_fails(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task_with_bootstrap(client, headers)
    assessment = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    import app.components.assessments.terminal_runtime as terminal_runtime

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *args, **kwargs: None})()

        def run_code(self, _code):
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("bootstrap-fail-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            return None

        def run_command(self, sandbox, command, cwd=None, timeout=30):
            if command == "python3 -m venv .venv":
                return {"stdout": "", "stderr": "venv failed", "exit_code": 2}
            return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_clone_assessment_branch_into_workspace", lambda *_args, **_kwargs: True)

    start = client.post(f"/api/v1/assessments/token/{assessment['token']}/start")
    assert start.status_code == 500
    assert "prepare assessment workspace" in start.json()["detail"].lower()


def test_claude_endpoint_returns_cursor_style_wrapper_response(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    assessment = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.assessments_runtime.candidate_claude_routes as candidate_claude_routes
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    import app.components.assessments.claude_budget as claude_budget
    import app.components.assessments.terminal_runtime as terminal_runtime

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *args, **kwargs: None})()

        def run_code(self, _code):
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("terminal-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            return None

    class FakeClaudeService:
        def __init__(self, _api_key):
            pass

        def chat(self, messages, system=None):
            assert messages[-1]["role"] == "user"
            assert system is not None
            return {
                "success": True,
                "content": "Cursor-style wrapper response",
                "tokens_used": 12,
                "input_tokens": 7,
                "output_tokens": 5,
            }

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ALLOW_GLOBAL_KEY_FALLBACK", True)
    monkeypatch.setattr(terminal_runtime.settings, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(claude_budget.settings, "ASSESSMENT_CLAUDE_BUDGET_DEFAULT_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_INPUT_COST_PER_MILLION_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_OUTPUT_COST_PER_MILLION_USD", 1.0)
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_clone_assessment_branch_into_workspace", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(candidate_claude_routes, "ClaudeService", FakeClaudeService)

    start = client.post(f"/api/v1/assessments/token/{assessment['token']}/start")
    assert start.status_code == 200
    assessment_id = start.json()["assessment_id"]

    claude = client.post(
        f"/api/v1/assessments/{assessment_id}/claude",
        json={"message": "help", "conversation_history": []},
        headers={"x-assessment-token": assessment["token"]},
    )
    assert claude.status_code == 200
    payload = claude.json()
    assert payload["success"] is True
    assert "Cursor-style wrapper response" in payload["content"]
    assert payload["claude_budget"]["enabled"] is True
    assert payload["claude_budget"]["used_usd"] > 0
    assert payload["claude_budget"]["remaining_usd"] < payload["claude_budget"]["limit_usd"]


def test_claude_endpoint_strips_internal_tool_markup_from_response(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    assessment = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.assessments_runtime.candidate_claude_routes as candidate_claude_routes
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    import app.components.assessments.claude_budget as claude_budget
    import app.components.assessments.terminal_runtime as terminal_runtime

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *args, **kwargs: None})()

        def run_code(self, _code):
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("terminal-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            return None

    class FakeClaudeService:
        def __init__(self, _api_key):
            pass

        def chat(self, messages, system=None):
            assert messages[-1]["role"] == "user"
            assert system is not None
            return {
                "success": True,
                "content": (
                    "I found the likely blockers and I am reviewing the repo context first.\n\n"
                    "<read_file>\n"
                    "<path>diagnostics/audit_findings.md</path>\n"
                    "</read_file>\n\n"
                    "<read_file>\n"
                    "<path>README.md</path>\n"
                    "</read_file>"
                ),
                "tokens_used": 20,
                "input_tokens": 12,
                "output_tokens": 8,
            }

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ALLOW_GLOBAL_KEY_FALLBACK", True)
    monkeypatch.setattr(terminal_runtime.settings, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(claude_budget.settings, "ASSESSMENT_CLAUDE_BUDGET_DEFAULT_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_INPUT_COST_PER_MILLION_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_OUTPUT_COST_PER_MILLION_USD", 1.0)
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_clone_assessment_branch_into_workspace", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(candidate_claude_routes, "ClaudeService", FakeClaudeService)

    start = client.post(f"/api/v1/assessments/token/{assessment['token']}/start")
    assert start.status_code == 200
    assessment_id = start.json()["assessment_id"]

    claude = client.post(
        f"/api/v1/assessments/{assessment_id}/claude",
        json={"message": "help", "conversation_history": []},
        headers={"x-assessment-token": assessment["token"]},
    )
    assert claude.status_code == 200
    payload = claude.json()
    assert payload["success"] is True
    assert "I found the likely blockers" in payload["content"]
    assert "<read_file>" not in payload["content"]
    assert "diagnostics/audit_findings.md" not in payload["content"]
    assert "README.md" not in payload["content"]


def test_terminal_ws_rejects_invalid_token(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    assessment = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    import app.components.assessments.terminal_runtime as terminal_runtime

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *args, **kwargs: None})()

        def run_code(self, _code):
            return {"stdout": "", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("terminal-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            return None

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_api.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(assessments_svc.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_ENABLED", True)
    monkeypatch.setattr(terminal_runtime.settings, "ASSESSMENT_TERMINAL_DEFAULT_MODE", "claude_cli_terminal")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_clone_assessment_branch_into_workspace", lambda *_args, **_kwargs: True)

    start = client.post(f"/api/v1/assessments/token/{assessment['token']}/start")
    assert start.status_code == 200
    assessment_id = start.json()["assessment_id"]

    with client.websocket_connect(f"/api/v1/assessments/{assessment_id}/terminal/ws?token=wrong-token") as websocket:
        payload = websocket.receive_json()
        assert payload["type"] == "error"
        assert "Invalid assessment token" in payload["message"]


def test_clone_assessment_branch_supports_execution_log_shape(monkeypatch):
    import app.components.assessments.service as assessments_svc

    class FakeRepoService:
        mock_root = None

        def __init__(self, _org, _token):
            pass

        def authenticated_repo_url(self, raw_repo_url):
            return raw_repo_url

    class FakeSandbox:
        def __init__(self):
            self.calls = 0

        def run_code(self, _code):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(logs=SimpleNamespace(stdout=['{"returncode": 0, "stderr": ""}']))
            return SimpleNamespace(logs=SimpleNamespace(stdout=['{"success": true, "stderr": ""}']))

    monkeypatch.setattr(assessments_svc, "AssessmentRepositoryService", FakeRepoService)

    assessment = SimpleNamespace(
        id=123,
        assessment_repo_url="https://github.com/taali-ai/data_eng_a_pipeline_reliability.git",
        assessment_branch="assessment/123",
    )
    task = SimpleNamespace(id=1, task_key="data_eng_a_pipeline_reliability")

    sandbox = FakeSandbox()
    assert assessments_svc._clone_assessment_branch_into_workspace(sandbox, assessment, task) is True
    assert sandbox.calls == 2


def test_clone_assessment_branch_fails_when_permission_repair_fails(monkeypatch):
    import app.components.assessments.service as assessments_svc

    class FakeRepoService:
        mock_root = None

        def __init__(self, _org, _token):
            pass

        def authenticated_repo_url(self, raw_repo_url):
            return raw_repo_url

    class FakeSandbox:
        def __init__(self):
            self.calls = 0

        def run_code(self, _code):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(logs=SimpleNamespace(stdout=['{"returncode": 0, "stderr": ""}']))
            return SimpleNamespace(logs=SimpleNamespace(stdout=['{"success": false, "stderr": "chmod failed"}']))

    monkeypatch.setattr(assessments_svc, "AssessmentRepositoryService", FakeRepoService)

    assessment = SimpleNamespace(
        id=456,
        assessment_repo_url="https://github.com/taali-ai/ai_eng_genai_production_readiness.git",
        assessment_branch="assessment/456",
    )
    task = SimpleNamespace(id=2, task_key="ai_eng_genai_production_readiness")

    sandbox = FakeSandbox()
    assert assessments_svc._clone_assessment_branch_into_workspace(sandbox, assessment, task) is False
    assert sandbox.calls == 2


def test_collect_git_evidence_supports_execution_log_shape():
    import app.components.assessments.service as assessments_svc

    payload = {
        "head_sha": "abc123",
        "status_porcelain": " M intelligence/analyzer.py",
        "diff_main": "diff --git a/file b/file",
        "diff_staged": "",
        "commits": "abc123 submit: candidate",
        "diff_base_ref": "origin/main",
    }

    class FakeSandbox:
        def run_code(self, _code):
            return SimpleNamespace(logs=SimpleNamespace(stdout=[json.dumps(payload)]))

    evidence = assessments_svc._collect_git_evidence_from_sandbox(FakeSandbox(), "/workspace/customer-intelligence-genai")
    assert evidence["head_sha"] == "abc123"
    assert evidence["status_porcelain"] == " M intelligence/analyzer.py"
    assert evidence["diff_base_ref"] == "origin/main"
