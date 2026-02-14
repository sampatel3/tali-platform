from tests.conftest import verify_user

def _register_and_login(client):
    client.post("/api/v1/auth/register", json={
        "email": "ops@example.com",
        "password": "testpass123",
        "full_name": "Ops",
        "organization_name": "Ops Org",
    })
    verify_user("ops@example.com")
    login_resp = client.post("/api/v1/auth/jwt/login", data={
        "username": "ops@example.com",
        "password": "testpass123",
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_task(client, headers, claude_budget_limit_usd=None):
    payload = {
        "name": "Sample Task",
        "description": "desc",
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": 30,
        "starter_code": "print('x')",
        "test_code": "def test_ok(): assert True",
        "task_key": "history-backfill",
        "role": "Data Engineer",
        "scenario": "Backfill missing account history",
        "repo_structure": {"files": {"src/backfill.py": "def run():\n    pass"}},
        "evaluation_rubric": {"correctness": 0.7, "readability": 0.3},
        "extra_data": {"expected_insights": ["cache repeated prompts"], "valid_solutions": ["redis cache"], "expected_approaches": {"schema_evolution": ["detect and add columns"]}},
    }
    if claude_budget_limit_usd is not None:
        payload["claude_budget_limit_usd"] = claude_budget_limit_usd
    resp = client.post("/api/v1/tasks", json=payload, headers=headers)
    return resp.json()


def _create_assessment(client, headers, task_id):
    resp = client.post("/api/v1/assessments", json={
        "candidate_email": "a@b.com",
        "candidate_name": "A B",
        "task_id": task_id,
        "duration_minutes": 30,
    }, headers=headers)
    return resp.json()


def test_delete_assessment(client):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    resp = client.delete(f"/api/v1/assessments/{a['id']}", headers=headers)
    assert resp.status_code == 204


def test_candidate_can_resume_in_progress_assessment(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("fake-new-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, sandbox):
            return None

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)

    first = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["assessment_id"] == a["id"]
    assert first_body["time_remaining"] > 0
    assert "scenario" in first_body["task"]
    assert "repo_structure" in first_body["task"]
    assert first_body["task"]["rubric_categories"] is not None
    assert first_body["task"]["evaluation_rubric"] is None

    second = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["assessment_id"] == a["id"]
    assert second_body["time_remaining"] >= 0


def test_timeline_telemetry_records_execute_and_prompt_events(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("fake-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def execute_code(self, sandbox, code):
            return {"stdout": "ok", "stderr": ""}

        def close_sandbox(self, sandbox):
            return None

    class FakeClaudeService:
        def __init__(self, api_key):
            self.api_key = api_key

        def chat(self, messages):
            return {"content": "fake-response", "input_tokens": 11, "output_tokens": 7, "tokens_used": 18}

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(integrations_adapters, "ClaudeService", FakeClaudeService)

    start = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert start.status_code == 200
    assessment_id = start.json()["assessment_id"]

    execute = client.post(
        f"/api/v1/assessments/{assessment_id}/execute",
        json={"code": "print('hello')"},
        headers={"x-assessment-token": a["token"]},
    )
    assert execute.status_code == 200

    prompt = client.post(
        f"/api/v1/assessments/{assessment_id}/claude",
        json={
            "message": "How should I debug this?",
            "conversation_history": [],
            "code_context": "print('hello')",
            "paste_detected": True,
            "browser_focused": False,
            "time_since_last_prompt_ms": 456,
        },
        headers={"x-assessment-token": a["token"]},
    )
    assert prompt.status_code == 200

    assessment = client.get(f"/api/v1/assessments/{assessment_id}", headers=headers)
    assert assessment.status_code == 200
    timeline = assessment.json().get("timeline") or []
    event_types = [e.get("event_type") for e in timeline if isinstance(e, dict)]
    assert "code_execute" in event_types
    assert "ai_prompt" in event_types

    code_event = next(e for e in timeline if e.get("event_type") == "code_execute")
    assert code_event["session_id"] == "fake-sandbox"
    assert code_event["code_length"] > 0
    assert code_event["latency_ms"] >= 0

    prompt_event = next(e for e in timeline if e.get("event_type") == "ai_prompt")
    assert prompt_event["paste_detected"] is True
    assert prompt_event["browser_focused"] is False
    assert prompt_event["time_since_last_prompt_ms"] == 456


def test_claude_budget_snapshot_and_limit_enforcement(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers, claude_budget_limit_usd=0.000005)
    a = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("budget-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, sandbox):
            return None

    class FakeClaudeService:
        call_count = 0

        def __init__(self, api_key):
            self.api_key = api_key

        def chat(self, messages, system=None):
            FakeClaudeService.call_count += 1
            return {
                "success": True,
                "content": "budget-aware-response",
                "input_tokens": 4,
                "output_tokens": 4,
                "tokens_used": 8,
            }

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(integrations_adapters, "ClaudeService", FakeClaudeService)

    start = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert start.status_code == 200
    start_payload = start.json()
    assert start_payload["task"]["claude_budget_limit_usd"] == 0.000005
    assert start_payload["claude_budget"]["enabled"] is True
    assert start_payload["claude_budget"]["is_exhausted"] is False
    assessment_id = start_payload["assessment_id"]

    first_prompt = client.post(
        f"/api/v1/assessments/{assessment_id}/claude",
        json={"message": "Please help", "conversation_history": []},
        headers={"x-assessment-token": a["token"]},
    )
    assert first_prompt.status_code == 200
    first_payload = first_prompt.json()
    assert first_payload["success"] is True
    assert first_payload["claude_budget"]["is_exhausted"] is True

    second_prompt = client.post(
        f"/api/v1/assessments/{assessment_id}/claude",
        json={"message": "Please help again", "conversation_history": []},
        headers={"x-assessment-token": a["token"]},
    )
    assert second_prompt.status_code == 200
    second_payload = second_prompt.json()
    assert second_payload["success"] is False
    assert second_payload["requires_budget_top_up"] is True
    assert second_payload["claude_budget"]["is_exhausted"] is True
    assert FakeClaudeService.call_count == 1


def test_start_materializes_repository_files_in_sandbox(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc

    class FakeFiles:
        def __init__(self):
            self.writes = []

        def write(self, path, content):
            self.writes.append((path, content))

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = FakeFiles()
            self.run_code_calls = []

        def run_code(self, code):
            self.run_code_calls.append(code)
            return {"stdout": "", "stderr": "", "error": None}

    holder = {}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            sandbox = FakeSandbox("sandbox-with-repo")
            holder["sandbox"] = sandbox
            return sandbox

        def connect_sandbox(self, sandbox_id):
            return holder["sandbox"]

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, sandbox):
            return None

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)

    start = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert start.status_code == 200

    sandbox = holder["sandbox"]
    assert any(path.endswith("/src/backfill.py") for path, _ in sandbox.files.writes)
    assert any("'git', 'init', '-b', 'candidate'" in code for code in sandbox.run_code_calls)


def test_execute_auto_submits_when_time_expires(client, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    import app.domains.assessments_runtime.routes as assessments_api
    import app.domains.integrations_notifications.adapters as integrations_adapters
    import app.components.assessments.service as assessments_svc
    from app.models.assessment import Assessment
    from tests.conftest import TestingSessionLocal
    from datetime import timedelta
    from app.components.assessments.repository import utcnow

    class FakeFiles:
        def write(self, path, content):
            return None

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = FakeFiles()

        def run_code(self, code):
            return {"stdout": "{}", "stderr": "", "error": None}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("s-timeout")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def execute_code(self, sandbox, code):
            return {"stdout": "ok", "stderr": ""}

        def close_sandbox(self, sandbox):
            return None

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)

    start = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert start.status_code == 200
    assessment_id = start.json()["assessment_id"]

    db = TestingSessionLocal()
    rec = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    rec.started_at = utcnow() - timedelta(minutes=31)
    db.commit()
    db.close()

    execute = client.post(
        f"/api/v1/assessments/{assessment_id}/execute",
        json={"code": "print('hello')"},
        headers={"x-assessment-token": a["token"]},
    )
    assert execute.status_code == 409
    assert "auto-submitted" in execute.json()["detail"]

    check = client.get(f"/api/v1/assessments/{assessment_id}", headers=headers)
    assert check.status_code == 200
    assert check.json()["completed_due_to_timeout"] is True
