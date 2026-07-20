import json
from pathlib import Path

from tests.conftest import verify_user
from app.models.organization import Organization
from app.models.user import User
from app.services.task_catalog import PERSISTED_TASK_SPEC_KEYS
from tests.candidate_proof_helpers import (
    CandidateProofSigner,
    compact_json_body,
    signed_candidate_headers,
)

_CANDIDATE_SESSION_KEY = "A" * 43
_PROOF_SIGNER = CandidateProofSigner()


def _start_candidate(client, token: str):
    path = f"/api/v1/assessments/token/{token}/start"
    raw_body = _PROOF_SIGNER.start_body(session_key=_CANDIDATE_SESSION_KEY)
    return client.post(
        path,
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            **_PROOF_SIGNER.headers(method="POST", path_and_query=path, raw_body=raw_body),
        },
    )


def _post_candidate(client, token: str, path: str, payload: object):
    raw_body = compact_json_body(payload)
    headers = signed_candidate_headers(
        _PROOF_SIGNER,
        token=token,
        session_key=_CANDIDATE_SESSION_KEY,
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
    )
    return client.post(
        path,
        content=raw_body,
        headers={"Content-Type": "application/json", **headers},
    )


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
    canonical_path = Path(__file__).resolve().parents[3] / "tasks" / "data_eng_bronze_ingestion.json"
    spec = json.loads(canonical_path.read_text(encoding="utf-8"))
    payload = {
        "name": spec["name"],
        "description": spec["scenario"][:500],
        "task_type": "debugging",
        "difficulty": "mid",
        "duration_minutes": spec["duration_minutes"],
        "starter_code": "print('x')",
        "test_code": "def test_ok(): assert True",
        "task_key": "history-backfill",
        "role": spec["role"],
        "scenario": spec["scenario"],
        "repo_structure": spec["repo_structure"],
        "evaluation_rubric": spec["evaluation_rubric"],
        "extra_data": {
            key: value
            for key, value in spec.items()
            if key not in PERSISTED_TASK_SPEC_KEYS
        },
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

    provisioning_calls = []

    def provision_once(_sandbox, assessment, _task):
        provisioning_calls.append(assessment.id)
        return True

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

        def run_command(self, _sandbox, _command, **_kwargs):
            return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_enforce_artifact_first_task", lambda _task: None)
    monkeypatch.setattr(
        assessments_svc,
        "_clone_assessment_branch_into_workspace",
        provision_once,
    )
    monkeypatch.setattr(assessments_svc, "_sandbox_workspace_is_ready", lambda *_args: True)

    first = _start_candidate(client, a["token"])
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["assessment_id"] == a["id"]
    assert first_body["time_remaining"] > 0
    assert "scenario" in first_body["task"]
    assert "repo_structure" in first_body["task"]
    assert first_body["task"]["rubric_categories"] is not None
    assert "evaluation_rubric" not in first_body["task"]

    second = _start_candidate(client, a["token"])
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["assessment_id"] == a["id"]
    assert second_body["time_remaining"] >= 0
    assert provisioning_calls == [a["id"]]


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

        def run_command(self, _sandbox, _command, **_kwargs):
            return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_enforce_artifact_first_task", lambda _task: None)
    monkeypatch.setattr(assessments_svc, "_sandbox_workspace_is_ready", lambda *_args: True)

    # Usage-based pricing (post-2026-04-29): starting an assessment no
    # longer deducts a flat credit. Charging happens per Claude call via
    # ``usage_metering_service.record_event``. Balance stays unchanged
    # by the start itself; ``credit_consumed_at`` is still stamped to
    # mark the assessment as billing-active.
    first = _start_candidate(client, a["token"])
    assert first.status_code == 200, first.text
    db.refresh(org)
    assert org.credits_balance == 2

    second = _start_candidate(client, a["token"])
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
    monkeypatch.setattr(assessments_svc, "_enforce_artifact_first_task", lambda _task: None)

    resp = _start_candidate(client, a["token"])
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
            if "file_count_limit_exceeded" in code:
                return {
                    "stdout": json.dumps(
                        {"files": {"src/main.py": "candidate work\n"}, "error": None}
                    ),
                    "stderr": "",
                    "error": None,
                }
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

        def run_command(self, _sandbox, _command, **_kwargs):
            return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(assessments_api.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(integrations_adapters, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc.settings, "E2B_API_KEY", "test-e2b-key")
    monkeypatch.setattr(assessments_svc, "E2BService", FakeE2BService)
    monkeypatch.setattr(assessments_svc, "_enforce_artifact_first_task", lambda _task: None)

    start = _start_candidate(client, a["token"])
    assert start.status_code == 200
    assessment_id = start.json()["assessment_id"]

    db = TestingSessionLocal()
    rec = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    rec.started_at = utcnow() - timedelta(minutes=31)
    db.commit()
    db.close()

    execute = _post_candidate(
        client,
        a["token"],
        f"/api/v1/assessments/{assessment_id}/execute",
        {"code": "print('hello')"},
    )
    assert execute.status_code == 409
    assert execute.json()["detail"] == "Assessment time expired and was auto-submitted"

    check = client.get(f"/api/v1/assessments/{assessment_id}", headers=headers)
    assert check.status_code == 200
    assert check.json()["completed_due_to_timeout"] is True
