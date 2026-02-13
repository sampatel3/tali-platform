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


def _create_task(client, headers):
    resp = client.post("/api/v1/tasks", json={
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
        "extra_data": {"expected_insights": ["cache repeated prompts"], "valid_solutions": ["redis cache"]},
    }, headers=headers)
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

    import app.api.v1.assessments as assessments_api
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
    monkeypatch.setattr(assessments_api, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)

    first = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["assessment_id"] == a["id"]
    assert first_body["time_remaining"] > 0
    assert "scenario" in first_body["task"]
    assert "repo_structure" in first_body["task"]
    assert first_body["task"]["expected_insights"] == ["cache repeated prompts"]
    assert first_body["task"]["valid_solutions"] == ["redis cache"]

    second = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["assessment_id"] == a["id"]
    assert second_body["time_remaining"] >= 0
