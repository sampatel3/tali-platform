from tests.conftest import verify_user
from app.models.organization import Organization
from app.models.user import User

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
            self.files = type("Files", (), {"write": lambda self, *a, **kw: None})()

        def run_code(self, _code):
            return {"stdout": '{"returncode": 0, "stderr": ""}'}

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


def test_start_assessment_consumes_credit_once(client, db, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    owner = db.query(User).filter(User.email == "ops@example.com").first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.credits_balance = 2
    db.commit()

    import app.domains.assessments_runtime.routes as assessments_api
    import app.components.assessments.service as assessments_svc

    class FakeSandbox:
        def __init__(self, sid):
            self.sandbox_id = sid
            self.files = type("Files", (), {"write": lambda self, *a, **kw: None})()

        def run_code(self, _code):
            return {"stdout": '{"returncode": 0, "stderr": ""}'}

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            return FakeSandbox("credit-sandbox")

        def connect_sandbox(self, sandbox_id):
            return FakeSandbox(sandbox_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, sandbox):
            return None

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)

    # Usage-based pricing (post-2026-04-29): starting an assessment no
    # longer deducts a flat credit. Charging happens per Claude call via
    # ``usage_metering_service.record_event``. Balance stays unchanged
    # by the start itself; ``credit_consumed_at`` is still stamped to
    # mark the assessment as billing-active.
    first = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert first.status_code == 200, first.text
    db.refresh(org)
    assert org.credits_balance == 2

    second = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert second.status_code == 200, second.text
    db.refresh(org)
    assert org.credits_balance == 2


def test_start_assessment_blocks_when_org_has_no_credits(client, db, monkeypatch):
    """Candidate-side start gate: zero balance + meter live → 402."""
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    owner = db.query(User).filter(User.email == "ops@example.com").first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.credits_balance = 0
    db.commit()

    import app.domains.assessments_runtime.routes as assessments_api
    import app.components.assessments.service as assessments_svc

    class FakeE2BService:
        def __init__(self, api_key):
            self.api_key = api_key

        def create_sandbox(self):
            raise AssertionError("Should not create sandbox without credits")

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "USAGE_METER_LIVE", True)
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)

    resp = client.post(f"/api/v1/assessments/token/{a['token']}/start")
    assert resp.status_code == 402
    assert resp.json()["detail"] == assessments_svc.CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE


def test_preview_assessment_reports_credit_block_when_org_has_no_credits(client, db, monkeypatch):
    headers = _register_and_login(client)
    task = _create_task(client, headers)
    a = _create_assessment(client, headers, task["id"])

    owner = db.query(User).filter(User.email == "ops@example.com").first()
    assert owner is not None
    org = db.query(Organization).filter(Organization.id == owner.organization_id).first()
    assert org is not None
    org.credits_balance = 0
    db.commit()

    import app.components.assessments.service as assessments_svc

    monkeypatch.setattr(assessments_svc.settings, "USAGE_METER_LIVE", True)

    resp = client.get(f"/api/v1/assessments/token/{a['token']}/preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["start_gate"] == {
        "can_start": False,
        "reason": "insufficient_credits",
        "message": assessments_svc.CANDIDATE_INSUFFICIENT_CREDITS_MESSAGE,
    }


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
            return {"stdout": '{"returncode": 0, "stderr": ""}', "stderr": "", "error": None}

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
