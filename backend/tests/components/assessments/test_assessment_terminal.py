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


def test_claude_endpoint_returns_terminal_hint_in_cli_mode(client, monkeypatch):
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

    claude = client.post(
        f"/api/v1/assessments/{assessment_id}/claude",
        json={"message": "help", "conversation_history": []},
        headers={"x-assessment-token": assessment["token"]},
    )
    assert claude.status_code == 501
    payload = claude.json()
    detail = payload["detail"]
    assert detail["requires_terminal"] is True
    assert "terminal-only" in detail["message"]


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

    class FakeLogs:
        stdout = ['{"returncode": 0, "stderr": ""}']

    class FakeExecution:
        logs = FakeLogs()

    class FakeSandbox:
        def run_code(self, _code):
            return FakeExecution()

    monkeypatch.setattr(assessments_svc, "AssessmentRepositoryService", FakeRepoService)

    assessment = SimpleNamespace(
        id=123,
        assessment_repo_url="https://github.com/taali-ai/data_eng_a_pipeline_reliability.git",
        assessment_branch="assessment/123",
    )
    task = SimpleNamespace(id=1, task_key="data_eng_a_pipeline_reliability")

    assert assessments_svc._clone_assessment_branch_into_workspace(FakeSandbox(), assessment, task) is True
